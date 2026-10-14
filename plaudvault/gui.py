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
import re
import secrets
import socket
import sqlite3
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from markdown_it import MarkdownIt

from .config import Config, load_config
from .crypto import WrongPassphrase, decrypt_bytes, unlock

# ponytail: copia de sync.MARKER; importar sync arrastra requests (no está en .venv-gui).
MARKER = ".plaud_id"
STATIC = Path(__file__).with_name("gui_static")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}  # sin SVG: puede llevar scripts
AUTO_LOCK_SECS = 15 * 60
CSP = ("default-src 'self'; media-src 'self' blob:; img-src 'self'; "
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


def render(md_path: Path, archive: Path) -> str:
    """Markdown → HTML. Las imágenes locales se reescriben a /files/…; las
    remotas se sustituyen por texto (ni siquiera se piden: no filtran nada)."""
    tokens = md.parse(md_path.read_text(encoding="utf-8"))
    for tok in tokens:
        for child in tok.children or []:
            if child.type != "image":
                continue
            src = child.attrGet("src") or ""
            target = (md_path.parent / src).resolve()
            if "://" not in src and target.is_relative_to(archive.resolve()) and target.is_file():
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


def fts_query(text: str) -> str:
    """Texto libre → consulta FTS5 segura: cada palabra entre comillas (AND).
    Sin esto, un `"` o un `-` del usuario es un error de sintaxis de FTS5."""
    return " ".join('"' + w.replace('"', '""') + '"' for w in text.split())


# ---------- audio: descifrado en RAM y Range ----------

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def range_response(data: bytes, header: str | None) -> Response:
    """Range (206) para que <audio> pueda saltar sin descargarlo todo otra vez."""
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    m = RANGE_RE.match(header or "")
    if not m or (not m[1] and not m[2]):
        return Response(data, media_type="audio/mpeg", headers=headers)
    size = len(data)
    if m[1]:
        start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
    else:  # bytes=-N → los últimos N
        start, end = max(size - int(m[2]), 0), size - 1
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(data[start:end + 1], status_code=206, media_type="audio/mpeg",
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

    def audio(self, rec_key: str, folder: Path) -> bytes:
        with self.lock:
            if self.key is None:
                raise HTTPException(401, "Vault bloqueado")
            self.last_use = time.monotonic()
            if self.cached and self.cached[0] == rec_key:
                return self.cached[1]
            pv = folder / "audio.pv"
            if pv.is_file():
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

def create_app(cfg: Config, login_token: str, allowed_hosts: set[str]) -> FastAPI:
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
        return FileResponse(STATIC / "index.html")

    @app.get("/static/{name}")
    def static(name: str):
        path = STATIC / name
        if name not in {"app.js", "app.css"}:
            raise HTTPException(404)
        return FileResponse(path)

    @app.get("/api/recordings")
    def recordings():
        found = scan(archive)
        indexed = {r[0] for r in db_query(
            cfg.paths.db, "SELECT plaud_file_id FROM recordings WHERE plaud_file_id IS NOT NULL")}
        out = []
        for key, folder in found.items():
            fid = (folder / MARKER).read_text().strip()
            group, _, title = key.rpartition("/")
            out.append({"key": key, "group": group, "title": title,
                        "indexed": fid in indexed})
        # Más recientes primero: el nombre empieza por YYYY-MM-DD_HHMM.
        out.sort(key=lambda r: r["title"], reverse=True)
        return {"recordings": out, "locked": vault.key is None}

    @app.get("/api/rec/{key:path}")
    def recording(key: str):
        folder = safe_folder(archive, key)
        return {"key": key, "docs": docs_of(folder),
                "has_audio": (folder / "audio.pv").is_file() or (folder / "audio.mp3").is_file()}

    @app.get("/api/doc/{key:path}")
    def doc(key: str, name: str):
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
        return FileResponse(target, media_type=IMAGE_TYPES[target.suffix.lower()],
                            headers={"Cache-Control": "private, max-age=300"})

    @app.get("/api/search")
    def search(q: str, limit: int = 30):
        if not q.strip():
            return {"results": []}
        rows = db_query(cfg.paths.db, """
            SELECT r.plaud_file_id AS fid, r.filename, t.model,
                   snippet(transcripts_fts, 0, char(2), char(3), ' … ', 14) AS frag
            FROM transcripts_fts
            JOIN transcripts t ON t.id = transcripts_fts.rowid
            JOIN recordings r ON r.id = t.recording_id
            WHERE transcripts_fts MATCH ? ORDER BY rank LIMIT ?""",
            (fts_query(q), min(limit, 100)))
        by_fid = {(f / MARKER).read_text().strip(): k for k, f in scan(archive).items()}
        # El fragmento va con marcadores \x02 \x03: el frontend los convierte en
        # <mark> con textContent, así el texto de la nota nunca se interpreta como HTML.
        return {"results": [{"key": by_fid.get(r["fid"]), "filename": r["filename"],
                             "kind": r["model"], "frag": r["frag"]} for r in rows]}

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
        folder = safe_folder(archive, key)
        return range_response(vault.audio(key, folder), request.headers.get("range"))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser(description="GUI local de PlaudVault (solo lectura)")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-p", "--port", type=int, default=0, help="0 = aleatorio")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    import uvicorn  # perezoso: solo hace falta al servir

    cfg = load_config(a.config)
    port = a.port or free_port()
    token = secrets.token_urlsafe(32)
    app = create_app(cfg, token, {f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=app.state.vault.auto_lock_loop, daemon=True).start()

    url = f"http://127.0.0.1:{port}/login?t={token}"
    print(f"PlaudVault GUI → {url}\n(el enlace sirve una sola vez; Ctrl+C para cerrar)", flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
