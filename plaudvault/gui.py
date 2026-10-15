"""gui.py — GUI web local de SOLO LECTURA (servidor en 127.0.0.1 + navegador).

    .venv-gui/bin/python -m plaudvault.gui [-c config.yaml] [--no-browser]

Por qué una web local y no una app nativa: el navegador ya sabe pintar
Markdown, imágenes y audio, y esta misma API será el sidecar de Tauri en la
fase 2 (ver docs/02_ARQUITECTURA.md).

Un servidor, aunque sea local, es una puerta abierta a cualquier proceso y a
cualquier web que visites (DNS rebinding). Cerraduras:
  1. Escucha solo en 127.0.0.1 y rechaza cualquier cabecera Host ajena.
  2. Token de un solo uso en la URL de arranque → cookie de sesión HttpOnly.
  3. CSP estricta: las notas vienen de la nube (contenido de terceros), así
     que ni HTML crudo, ni scripts inline, ni imágenes remotas.
  4. Solo se sirven ficheros dentro de archive/, de grabaciones publicadas.
  5. La clave AES vive solo en memoria; el audio se descifra en RAM.

Concurrencia con `sync`: la DB se abre en mode=ro, con consultas cortas y
fetchall() (nunca un cursor vivo). Ver docs/SESION_3_GUI.md.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from markdown_it import MarkdownIt

from .config import Config, load_config
from .crypto import WrongPassphrase, decrypt_bytes, unlock
from .db import fts_query

# ponytail: copia de sync.MARKER; importar sync arrastra requests (no está en .venv-gui).
MARKER = ".plaud_id"
STATIC = Path(__file__).with_name("gui_static")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}  # sin SVG: puede llevar scripts
AUTO_LOCK_SECS = 15 * 60
CSP = ("default-src 'self'; media-src 'self'; img-src 'self'; "  # sin blob: (ver spike)
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

# html=False: un <script> dentro de una nota se muestra como texto, no se ejecuta.
md = MarkdownIt("commonmark", {"html": False}).enable("table")


# ---------- sistema de ficheros: qué grabaciones están publicadas ----------

def scan(archive: Path) -> dict[str, Path]:
    """{clave: carpeta} de cada grabación publicada. La clave es la ruta relativa
    a archive/ (vale para MM-YY/x y para <Carpeta Plaud>/MM-YY/x de reclassify).
    Una carpeta *.partial es un import a medias: se ignora ella y todo su interior."""
    out = {}
    for marker in archive.rglob(MARKER):
        folder = marker.parent
        rel = folder.relative_to(archive)
        if not any(p.endswith(".partial") for p in rel.parts):
            out[rel.as_posix()] = folder
    return out


def safe_folder(archive: Path, key: str) -> Path:
    """Clave del cliente → carpeta, o 404. Bloquea ../ y carpetas no publicadas."""
    folder = (archive / key).resolve()
    if (not folder.is_relative_to(archive.resolve()) or folder == archive.resolve()
            or any(p.endswith(".partial") for p in folder.relative_to(archive.resolve()).parts)
            or not (folder / MARKER).is_file()):
        raise HTTPException(404, "Grabación no encontrada")
    return folder


def docs_of(folder: Path) -> list[str]:
    """Documentos en el orden en que se leen: transcript, outline, notas."""
    docs = [n for n in ("transcript.md", "outline.md") if (folder / n).is_file()]
    return docs + sorted(f"notes/{p.name}" for p in (folder / "notes").glob("*.md"))


def render(md_path: Path, archive: Path, text: str | None = None) -> str:
    """Markdown → HTML. Las imágenes locales se reescriben a /files/…; las
    remotas se sustituyen por texto (ni siquiera se piden: no filtran nada)."""
    tokens = md.parse(md_path.read_text(encoding="utf-8") if text is None else text)
    for tok in tokens:
        for child in tok.children or []:
            if child.type != "image":
                continue
            src = child.attrGet("src") or ""
            target = (md_path.parent / src).resolve()
            if text is None and "://" not in src and target.is_relative_to(archive.resolve()) and target.is_file():
                child.attrSet("src", "/files/" + quote(target.relative_to(archive.resolve()).as_posix()))
            else:
                child.type, child.children = "text", None
                child.content = f"[imagen externa no cargada: {src}]"
    return md.renderer.render(tokens, md.options, {})


# ---------- DB en solo lectura ----------

def db_query(db_path: Path, sql: str, params=()) -> list[sqlite3.Row]:
    """Conexión nueva, consulta, fetchall, cerrar. Así el bloqueo SHARED dura
    milisegundos y el commit del sync nunca espera por nosotros."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ---------- audio: descifrado en RAM y Range ----------

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def range_response(data: bytes, header: str | None, media_type: str = "audio/mpeg") -> Response:
    """Range (206) para que <audio> pueda saltar sin descargarlo todo otra vez."""
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    m = RANGE_RE.match(header or "")
    if not m or (not m[1] and not m[2]):
        return Response(data, media_type=media_type, headers=headers)
    size = len(data)
    if m[1]:
        start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
    else:  # bytes=-N → los últimos N
        start, end = max(size - int(m[2]), 0), size - 1
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(data[start:end + 1], status_code=206, media_type=media_type,
                    headers=headers)


class Vault:
    """Estado de la sesión: clave en memoria + el último audio descifrado."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.key: bytes | None = None
        # ponytail: caché de UN audio (clave → bytes) porque <audio> hace varias
        # peticiones Range; sin ella se descifraría el fichero entero en cada una.
        self.cached: tuple[str, bytes] | None = None
        self.last_use = 0.0

    def unlock(self, passphrase: str) -> None:
        key = unlock(self.cfg.salt_path, self.cfg.keycheck_path, passphrase)
        with self.lock:
            self.key, self.last_use = key, time.monotonic()

    def lock_now(self) -> None:
        with self.lock:
            self.key = self.cached = None  # Python no garantiza borrar la RAM; soltamos la referencia

    def audio(self, rec_key: str, folder: Path, audio_path: Path | None = None) -> bytes:
        with self.lock:
            pv = audio_path or folder / "audio.pv"
            encrypted = pv.suffix.lower() == ".pv" and pv.is_file()
            if self.key is None and (self.cfg.crypto_enabled or encrypted):
                raise HTTPException(401, "Vault bloqueado")
            self.last_use = time.monotonic()
            if self.cached and self.cached[0] == rec_key:
                return self.cached[1]
            if audio_path is not None and pv.suffix.lower() != ".pv":
                data = pv.read_bytes()
            elif pv.is_file():
                data = decrypt_bytes(self.key, pv.read_bytes())
            elif (folder / "audio.mp3").is_file():  # vault con crypto desactivado
                data = (folder / "audio.mp3").read_bytes()
            else:
                raise HTTPException(404, "Esta grabación no tiene audio")
            self.cached = (rec_key, data)
            return data

    def auto_lock_loop(self) -> None:
        while True:
            time.sleep(30)
            if self.key is not None and time.monotonic() - self.last_use > AUTO_LOCK_SECS:
                self.lock_now()


# ---------- la app ----------

def create_app(cfg: Config, login_token: str, allowed_hosts: set[str],
               frontend_dir: Path | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    archive = cfg.paths.archive
    vault = Vault(cfg)
    app.state.vault = vault
    sessions: set[str] = set()
    pending = {"token": login_token}  # de un solo uso: tras el login deja de valer

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # 1) DNS rebinding: una web maliciosa que resuelve a 127.0.0.1 llega con SU Host.
        if request.headers.get("host") not in allowed_hosts:
            return Response("Host no permitido", status_code=400)
        # 2) Sesión: todo menos /login exige la cookie.
        if request.url.path != "/login" and request.cookies.get("pv_session") not in sessions:
            return Response("Abre la URL que imprimió `plaudvault.gui` al arrancar.",
                            status_code=401)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    def local_recording(key: str):
        if not re.fullmatch(r"local/[1-9][0-9]*", key):
            raise HTTPException(404, "Grabación no encontrada")
        rows = db_query(cfg.paths.db,
                        "SELECT * FROM recordings WHERE id = ? AND plaud_file_id IS NULL",
                        (int(key.split("/")[1]),))
        if not rows or local_path(rows[0]) is None:
            raise HTTPException(404, "Grabación no encontrada")
        return rows[0]

    def local_path(row):
        # Texto y audio sin crypto no guardan encrypted_path en Pipeline.
        filename = row["filename"]
        if Path(filename).name != filename or filename in {".", ".."}:
            return None
        path = (Path(row["encrypted_path"]) if row["encrypted_path"] else
                archive / f'{row["id"]:06d}_{filename}')
        target = path.resolve()
        root = archive.resolve()
        if (not target.is_relative_to(root) or not target.is_file()
                or any(part.endswith(".partial") for part in path.parts)
                or any(part.endswith(".partial") for part in target.relative_to(root).parts)):
            return None
        return target

    def local_transcript(row):
        rows = db_query(cfg.paths.db,
                        "SELECT content FROM transcripts WHERE recording_id = ? ORDER BY id LIMIT 1",
                        (row["id"],))
        return rows[0]["content"] if rows else None

    @app.get("/login")
    def login(t: str = ""):
        if not pending["token"] or not secrets.compare_digest(t, pending["token"]):
            raise HTTPException(401, "Token no válido o ya usado")
        pending["token"] = None
        sid = secrets.token_urlsafe(32)
        sessions.add(sid)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("pv_session", sid, httponly=True, samesite="strict")
        return resp

    @app.get("/")
    def index():
        return FileResponse((frontend_dir or STATIC) / "index.html")

    if frontend_dir is not None:
        app.mount("/desktop", StaticFiles(directory=frontend_dir), name="desktop")

    @app.get("/static/{name}")
    def static(name: str):
        path = STATIC / name
        if name not in {"app.js", "app.css"}:
            raise HTTPException(404)
        return FileResponse(path)

    @app.get("/api/recordings")
    def recordings():
        found = scan(archive)
        rows = db_query(cfg.paths.db, "SELECT * FROM recordings")
        indexed = {r["plaud_file_id"]: r for r in rows if r["plaud_file_id"] is not None}
        out = []
        for key, folder in found.items():
            fid = (folder / MARKER).read_text().strip()
            group, _, title = key.rpartition("/")
            out.append({"key": key, "group": group, "title": title,
                        "indexed": fid in indexed, "source": "plaud",
                        "duration_secs": indexed[fid]["duration_secs"] if fid in indexed else None,
                        "ingested_at": indexed[fid]["ingested_at"] if fid in indexed else None})
        for row in rows:
            if row["plaud_file_id"] is None and local_path(row) is not None:
                out.append({"key": f'local/{row["id"]}', "group": "Local",
                            "title": row["filename"], "indexed": True, "source": "local",
                            "duration_secs": row["duration_secs"], "ingested_at": row["ingested_at"]})
        out.sort(key=lambda r: (r["ingested_at"] or "", r["title"]), reverse=True)
        return {"recordings": out, "locked": cfg.crypto_enabled and vault.key is None,
                "crypto_enabled": cfg.crypto_enabled}

    @app.get("/api/rec/{key:path}")
    def recording(key: str):
        if key.startswith("local/"):
            row = local_recording(key)
            return {"key": key, "docs": ["transcript.md"] if local_transcript(row) is not None else [],
                    "has_audio": row["source_type"] == "audio"}
        folder = safe_folder(archive, key)
        return {"key": key, "docs": docs_of(folder),
                "has_audio": (folder / "audio.pv").is_file() or (folder / "audio.mp3").is_file()}

    @app.get("/api/doc/{key:path}")
    def doc(key: str, name: str):
        if key.startswith("local/"):
            row = local_recording(key)
            text = local_transcript(row)
            if name != "transcript.md" or text is None:
                raise HTTPException(404, "Documento no encontrado")
            return {"html": render(archive / "transcript.md", archive, text=text)}
        folder = safe_folder(archive, key)
        if name not in docs_of(folder):  # lista blanca: nada de rutas libres
            raise HTTPException(404, "Documento no encontrado")
        return {"html": render(folder / name, archive)}

    @app.get("/files/{path:path}")
    def files(path: str):
        target = (archive / path).resolve()
        rel_parts = target.relative_to(archive.resolve()).parts if target.is_relative_to(archive.resolve()) else ()
        if (not rel_parts or target.suffix.lower() not in IMAGE_TYPES or not target.is_file()
                or any(p.endswith(".partial") for p in rel_parts)):
            raise HTTPException(404)
        # Sin cabecera propia: el middleware pone no-store (nada en la caché de disco).
        return FileResponse(target, media_type=IMAGE_TYPES[target.suffix.lower()])

    @app.get("/api/search")
    def search(q: str, limit: int = 30):
        if not q.strip():
            return {"results": []}
        rows = db_query(cfg.paths.db, """
            SELECT r.id, r.plaud_file_id AS fid, r.filename, t.model,
                   snippet(transcripts_fts, 0, char(2), char(3), ' … ', 14) AS frag
            FROM transcripts_fts
            JOIN transcripts t ON t.id = transcripts_fts.rowid
            JOIN recordings r ON r.id = t.recording_id
            WHERE transcripts_fts MATCH ? ORDER BY rank LIMIT ?""",
            (fts_query(q), min(limit, 100)))
        by_fid = {(f / MARKER).read_text().strip(): k for k, f in scan(archive).items()}
        local_keys = {r["id"]: f'local/{r["id"]}' for r in
                      db_query(cfg.paths.db, "SELECT * FROM recordings WHERE plaud_file_id IS NULL")
                      if local_path(r) is not None}
        # El fragmento va con marcadores \x02 \x03: el frontend los convierte en
        # <mark> con textContent, así el texto de la nota nunca se interpreta como HTML.
        return {"results": [{"key": by_fid.get(r["fid"]) if r["fid"] is not None else local_keys.get(r["id"]), "filename": r["filename"],
                             "kind": r["model"], "frag": r["frag"]} for r in rows
                            if r["fid"] is not None or r["id"] in local_keys]}

    @app.post("/api/unlock")
    def unlock_vault(body: dict):
        # Endpoint síncrono → FastAPI lo corre en un hilo: Argon2id (~1 s) no congela el resto.
        try:
            vault.unlock(str(body.get("passphrase", "")))
        except (WrongPassphrase, FileNotFoundError) as exc:
            raise HTTPException(401, "Passphrase incorrecta") from exc
        return {"locked": False}

    @app.post("/api/lock")
    def lock_vault():
        vault.lock_now()
        return {"locked": True}

    @app.get("/api/audio/{key:path}")
    def audio(key: str, request: Request):
        if key.startswith("local/"):
            row = local_recording(key)
            if row["source_type"] != "audio":
                raise HTTPException(404, "Esta grabación no tiene audio")
            path = local_path(row)
            media_type = ({".wav": "audio/wav", ".flac": "audio/flac", ".m4a": "audio/mp4",
                           ".ogg": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mpeg"}
                          .get(Path(row["filename"]).suffix.lower())
                          or mimetypes.guess_type(row["filename"])[0] or "application/octet-stream")
            return range_response(vault.audio(key, archive, path), request.headers.get("range"), media_type)
        folder = safe_folder(archive, key)
        return range_response(vault.audio(key, folder), request.headers.get("range"))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app


# ---------- navegador efímero: cero rastro en disco ----------
#
# no-store le PIDE al navegador que no guarde nada, pero cada uno decide: historial,
# autocompletado, gestor de contraseñas, caché multimedia… La única garantía que
# no depende del navegador es darle un perfil que vive en RAM (tmpfs) y borrarlo
# al cerrar. Solo familia Chromium: Firefox no permite abrir con un perfil nuevo
# en modo app de forma fiable.

CHROMIUM_FAMILY = ("thorium-browser", "chromium", "chromium-browser", "google-chrome",
                   "google-chrome-stable", "brave-browser", "microsoft-edge")


def find_browser(explicit: str | None = None) -> str | None:
    if explicit:
        return shutil.which(explicit)
    return next((p for b in CHROMIUM_FAMILY if (p := shutil.which(b))), None)


def ram_dir() -> Path | None:
    """Un directorio escribible que sea de verdad RAM (tmpfs/ramfs), comprobado en
    /proc/mounts: que exista /dev/shm no basta para afirmarlo."""
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            fstype = {parts[1]: parts[2] for parts in (line.split() for line in f)}
    except OSError:  # macOS/Windows: sin /proc
        return None
    for cand in ("/dev/shm", os.environ.get("XDG_RUNTIME_DIR")):
        if cand and fstype.get(cand) in ("tmpfs", "ramfs") and os.access(cand, os.W_OK):
            return Path(cand)
    return None


def browser_cmd(browser: str, profile: Path, url: str) -> list[str]:
    # NUNCA --no-sandbox: el sandbox de Chromium (seccomp + namespaces) encierra a
    # los renderers, que son los que interpretan contenido de terceros (las notas).
    return [browser,
            f"--user-data-dir={profile}",          # perfil entero (historial, contraseñas…) en RAM
            f"--disk-cache-dir={profile / 'cache'}",  # la caché HTTP/multimedia, también
            f"--app={url}",                        # ventana sin barra de direcciones ni pestañas
            # Red aislada: todo sale por un proxy que no existe (puerto 9 = discard).
            # Chromium deja pasar 127.0.0.1 sin proxy, así que solo llega a la GUI:
            # ni telemetría, ni actualizaciones, ni webs externas desde una nota.
            "--proxy-server=127.0.0.1:9",
            "--disable-background-networking", "--disable-component-update",
            "--disable-sync", "--disable-extensions", "--no-pings",
            "--no-first-run", "--no-default-browser-check"]


def browser_env() -> dict[str, str]:
    """Entorno sin las variables con las que los lanzadores de Debian/Ubuntu
    añaden flags por su cuenta (CHROMIUM_FLAGS podría traer --no-sandbox)."""
    return {k: v for k, v in os.environ.items() if k not in ("CHROMIUM_FLAGS", "CHROME_FLAGS")}


def group_pids(pgid: int) -> list[int]:
    pids = []
    for d in Path("/proc").iterdir():
        try:
            if d.name.isdigit() and os.getpgid(int(d.name)) == pgid:
                pids.append(int(d.name))
        except (ProcessLookupError, PermissionError):
            pass
    return pids


def sandbox_problem(pgid: int, timeout: float = 15) -> str | None:
    """Comprueba en /proc que el navegador lanzado está de verdad en sandbox.
    Devuelve None si todo bien, o el motivo del fallo. No nos fiamos de los
    argumentos: un script lanzador o /etc/chromium.d pueden añadir flags."""
    deadline = time.monotonic() + timeout
    unsandboxed: str | None = None
    while time.monotonic() < deadline:
        renderers, unsandboxed = 0, None
        for pid in group_pids(pgid):
            try:
                # Chromium reescribe el título de sus procesos: los argumentos pueden
                # venir separados por espacios en vez de por \0. Partimos por ambos.
                cmdline = re.split(rb"[\0 ]", Path(f"/proc/{pid}/cmdline").read_bytes())
                status = Path(f"/proc/{pid}/status").read_text()
            except OSError:
                continue
            if b"--no-sandbox" in cmdline:
                return f"el proceso {pid} se lanzó con --no-sandbox"
            if b"--type=renderer" in cmdline:
                renderers += 1
                seccomp = next((ln.split()[1] for ln in status.splitlines()
                                if ln.startswith("Seccomp:")), "0")
                if seccomp != "2":  # 2 = filtro seccomp-bpf activo
                    # Un renderer recién nacido activa seccomp unos milisegundos
                    # después de arrancar: solo es fallo si sigue así al acabar el plazo.
                    unsandboxed = f"el renderer {pid} no tiene seccomp activo"
        if renderers and unsandboxed is None:
            return None
        time.sleep(0.3)
    return unsandboxed or "no apareció ningún renderer que comprobar"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser(description="GUI local de PlaudVault (solo lectura)")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-p", "--port", type=int, default=0, help="0 = aleatorio")
    ap.add_argument("--browser", help="ejecutable de la familia Chromium (por defecto: el primero instalado)")
    ap.add_argument("--no-browser", action="store_true",
                    help="no abrir navegador; tú abres la URL (sin garantía de cero rastro)")
    a = ap.parse_args()

    import uvicorn  # perezoso: solo hace falta al servir

    cfg = load_config(a.config)
    port = a.port or free_port()
    token = secrets.token_urlsafe(32)
    app = create_app(cfg, token, {f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=app.state.vault.auto_lock_loop, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    url = f"http://127.0.0.1:{port}/login?t={token}"

    browser = None if a.no_browser else find_browser(a.browser)
    ram = ram_dir()
    if browser is None or ram is None:
        why = ("--no-browser" if a.no_browser else
               "no hay navegador de la familia Chromium" if browser is None else
               "no hay un directorio en RAM (tmpfs) para el perfil")
        print(f"⚠️  Sin navegador efímero ({why}). Si abres la URL en tu navegador normal, "
              "este puede guardar en disco historial, caché o la passphrase.\n"
              "   Usa al menos una ventana privada.", flush=True)
        print(f"PlaudVault GUI → {url}\n(el enlace sirve una sola vez; Ctrl+C para cerrar)", flush=True)
        server.run()
        return

    profile = Path(tempfile.mkdtemp(prefix="plaudvault-gui-", dir=ram))  # 0700: solo tu usuario
    proc = None
    try:
        proc = subprocess.Popen(browser_cmd(browser, profile, url), env=browser_env(),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        if problem := sandbox_problem(proc.pid):
            print(f"❌ Navegador cerrado: sin sandbox ({problem}). No se abre la GUI.", flush=True)
            return  # el finally mata el grupo y borra el perfil
        print(f"PlaudVault GUI abierta en {Path(browser).name} con perfil en RAM "
              "(se borra al cerrar). Cierra la ventana o pulsa Ctrl+C.", flush=True)

        def stop_when_browser_closes():
            started = time.monotonic()
            proc.wait()
            # Si el lanzador sale enseguida (p. ej. delega en otro proceso), no
            # cerramos el servidor: se cierra con Ctrl+C.
            if time.monotonic() - started > 5:
                server.should_exit = True

        threading.Thread(target=stop_when_browser_closes, daemon=True).start()
        server.run()
    finally:
        if proc is not None:
            kill_group(proc)  # el grupo entero: los hijos sobreviven al lanzador
        shutil.rmtree(profile, ignore_errors=True)


def kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM al grupo, espera hasta 5 s a que se vacíe, y SIGKILL si no. Así
    ningún proceso del navegador sigue escribiendo en el perfil que vamos a borrar."""
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
        for _ in range(50):
            time.sleep(0.1)
            proc.poll()         # recoge al lanzador: un zombie también cuenta como vivo
            os.killpg(pgid, 0)  # ProcessLookupError = ya no queda nadie
        os.killpg(pgid, signal.SIGKILL)
        time.sleep(0.3)
    except ProcessLookupError:
        pass


if __name__ == "__main__":
    main()
