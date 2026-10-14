"""sync.py — Importa grabaciones de la nube de Plaud al vault local.

Por cada grabación crea una carpeta autocontenida, agrupada por mes:

    archive/MM-YY/YYYY-MM-DD_HHMM_<nombre>/
        audio.pv            # mp3 cifrado (audio.mp3 si crypto está desactivado)
        transcript.md       # transcript pulido, con [MM:SS] y speaker
        outline.md          # índice de temas
        notes/NN_<titulo>.md  # TODAS las notas: una por plantilla/tipo
        assets/*.png        # infografías, con los enlaces del .md reescritos

Por qué carpetas autocontenidas: se pueden abrir tal cual en Obsidian o
cualquier visor Markdown, sin depender de PlaudVault ni de la nube.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from .config import Config
from .crypto import encrypt_bytes
from .db import VaultDB
from .plaud_client import PlaudClient

logger = logging.getLogger("plaudvault.sync")

# Tipos de contenido de Plaud (vistos en la API real, ver docs/).
TRANSCRIPT_TYPES = ("transaction_polish", "transaction")  # por preferencia
OUTLINE_TYPE = "outline"
MARKER = ".plaud_id"  # demuestra que una carpeta es de esta grabación
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)([^)]*)\)")
UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


# ---------- nombres y formato (funciones puras: fáciles de testear) ----------

def slug(text: str, maxlen: int = 60) -> str:
    """Nombre seguro para el sistema de ficheros. Conserva tildes y espacios
    (es tu vault, se lee mejor), quita solo lo que rompe rutas."""
    clean = UNSAFE.sub("-", text).strip(" .-")
    return clean[:maxlen].rstrip(" .-") or "sin_titulo"


def month_dir(start_ms: int) -> str:
    """'10-26' para octubre de 2026. Hora local de esta máquina."""
    # ponytail: hora local del PC, no la zona de la grabación; si grabas en
    # otro huso, usar el campo `timezone` que devuelve Plaud.
    return datetime.fromtimestamp(start_ms / 1000).strftime("%m-%y")


def folder_name(start_ms: int, filename: str) -> str:
    stamp = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d_%H%M")
    return f"{stamp}_{slug(filename)}"


def fmt_ts(ms: int | float | None) -> str:
    s = int((ms or 0) // 1000)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_transcript(segments) -> str:
    if isinstance(segments, str):
        return segments
    lines = [f"**[{fmt_ts(seg.get('start_time'))}] {seg.get('speaker') or '?'}:** "
             f"{(seg.get('content') or '').strip()}" for seg in segments]
    return "\n\n".join(lines) + "\n"


def render_outline(items) -> str:
    if isinstance(items, str):
        return items
    return "\n".join(f"- [{fmt_ts(i.get('start_time'))}] {i.get('topic', '')}"
                     for i in items) + "\n"


def render_note(body) -> str:
    """Las notas de plantilla son Markdown: se guardan tal cual. Si algún tipo
    llega como JSON (marcas, highlights…), se conserva íntegro en un bloque."""
    if isinstance(body, str):
        return body
    # ponytail: JSON crudo en bloque de código; formatear por tipo cuando
    # sepamos qué campos trae cada uno.
    return "```json\n" + json.dumps(body, ensure_ascii=False, indent=2) + "\n```\n"


def localize_images(md: str, mapping: dict, assets: Path, fetch, used: dict) -> str:
    """Descarga las imágenes que el Markdown referencia vía download_path_mapping
    y reescribe cada enlace a ../assets/<fichero>. `used` (ref → nombre local)
    se comparte entre notas para no bajar dos veces la misma imagen."""
    def repl(m):
        alt, ref, rest = m.groups()
        # Imagen de Plaud (ruta interna → URL de S3) o imagen web directa.
        url = mapping.get(ref) or (ref if ref.startswith(("http://", "https://")) else None)
        if url is None:
            return m.group(0)  # ruta desconocida: se deja igual
        if ref not in used:
            try:
                used[ref] = save_asset(ref.split("?")[0], url, assets, fetch, used)
            except Exception as exc:
                # Una imagen caída no debe tirar la nota entera.
                logger.warning("  Imagen no descargada (%s): se deja el enlace", exc)
                return m.group(0)
        return f"![{alt}](../assets/{used[ref]}{rest})"
    return IMG_RE.sub(repl, md)


def save_asset(ref: str, url: str, assets: Path, fetch, used: dict) -> str:
    name = slug(ref.rsplit("/", 1)[-1].split("@")[-1], 80)
    stem, dot, ext = name.rpartition(".")
    taken = set(used.values())
    n = 1
    while name in taken:  # dos imágenes distintas con el mismo nombre
        n += 1
        name = f"{stem}_{n}.{ext}" if dot else f"{name}_{n}"
    assets.mkdir(exist_ok=True)
    (assets / name).write_bytes(fetch(url))
    return name


# ---------- orquestación ----------

class PlaudSync:
    def __init__(self, cfg: Config, db: VaultDB, client: PlaudClient,
                 key: bytes | None, transcriber=None):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.key = key                  # None = crypto desactivado
        self.transcriber = transcriber  # solo si --transcribe-missing

    def run(self, limit: int | None = None) -> tuple[int, int, int]:
        """Devuelve (importadas, ya existentes, fallidas)."""
        done = skipped = failed = 0
        for f in self.client.iter_files():
            if limit is not None and done >= limit:
                break
            if self.db.has_plaud_file(f["id"]):
                skipped += 1
                continue
            try:
                self.import_file(f)
                done += 1
            except Exception:
                # Una grabación rota no frena el resto; su .partial se limpia
                # en el próximo sync.
                failed += 1
                logger.exception("Error importando %s", f.get("filename"))
        return done, skipped, failed

    def import_file(self, f: dict) -> int:
        fid, start = f["id"], f["start_time"]
        month = self.cfg.paths.archive / month_dir(start)
        final = month / folder_name(start, f.get("filename") or fid)
        if final.exists() and not _is_ours(final, fid):
            final = final.with_name(f"{final.name}_{fid[:8]}")  # colisión de nombre
        work = final.with_name(final.name + ".partial")
        shutil.rmtree(work, ignore_errors=True)  # resto de un sync interrumpido
        work.mkdir(parents=True)
        (work / MARKER).write_text(fid)

        logger.info("Importando %s", final.relative_to(self.cfg.paths.archive))
        detail = self.client.file_detail(fid)
        texts = self._write_contents(detail, work)
        audio_path = self._write_audio(fid, detail, work, texts)

        # Carpeta completa → la publicamos de golpe. Si existía una versión
        # anterior NUESTRA a medio registrar, se sustituye.
        if final.exists():
            shutil.rmtree(final)
        work.rename(final)

        # Una sola transacción: la grabación entra en la DB con todos sus textos
        # o no entra. Si fallara a medias (p. ej. "database is locked"), `with`
        # hace rollback; sin él, un commit posterior guardaría la grabación sin
        # transcripts y has_plaud_file() impediría reimportarla jamás.
        with self.db.conn:
            rec_id = self.db.add_recording(
                filename=(f.get("filename") or fid) + (".mp3" if audio_path else ""),
                sha256=f"plaud:{fid}",  # ponytail: id de Plaud como huella; no
                # detecta el mismo mp3 ingerido también por inbox/
                source_type="audio" if audio_path else "text",
                duration_secs=(f.get("duration") or 0) / 1000 or None,
                encrypted_path=str(final / audio_path) if audio_path else None,
                plaud_file_id=fid,
                commit=False,
            )
            for model, text in texts:
                self.db.add_transcript(rec_id, text, self.cfg.language, model, commit=False)
        return rec_id

    def _write_contents(self, detail: dict, work: Path) -> list[tuple[str, str]]:
        """Escribe transcript, outline y notas. Devuelve [(modelo, texto)] para
        indexar en FTS: primero el transcript, que es lo que muestra `show`."""
        inline = {c.get("data_id"): c.get("data_content")
                  for c in detail.get("pre_download_content_list") or []}
        mapping = detail.get("download_path_mapping") or {}
        items = detail.get("content_list") or []
        by_type = {i.get("data_type"): i for i in items}
        texts: list[tuple[str, str]] = []
        used: dict[str, str] = {}

        def body_of(item):
            # Plaud a veces adelanta el contenido inline; si no, se baja de S3.
            if inline.get(item.get("data_id")):
                raw = inline[item["data_id"]]
                try:
                    return json.loads(raw)
                except ValueError:
                    return raw
            return self.client.fetch_json_or_text(item["data_link"])

        ready = [i for i in items if i.get("task_status") == 1
                 and (i.get("data_link") or inline.get(i.get("data_id")))]
        for i in items:
            if i not in ready:
                logger.warning("  Omitido %s (status=%s)", i.get("data_type"), i.get("task_status"))

        transcript = next((by_type[t] for t in TRANSCRIPT_TYPES
                           if t in by_type and by_type[t] in ready), None)
        if transcript:
            text = render_transcript(body_of(transcript))
            (work / "transcript.md").write_text(text, encoding="utf-8")
            texts.append((f"plaud-{transcript['data_type']}", text))

        if OUTLINE_TYPE in by_type and by_type[OUTLINE_TYPE] in ready:
            (work / "outline.md").write_text(
                render_outline(body_of(by_type[OUTLINE_TYPE])), encoding="utf-8")

        notes = [i for i in ready
                 if i.get("data_type") not in TRANSCRIPT_TYPES + (OUTLINE_TYPE,)]
        for n, item in enumerate(notes, 1):
            title = item.get("data_title") or item.get("data_tab_name") or item["data_type"]
            md = render_note(body_of(item))
            md = localize_images(md, mapping, work / "assets", self.client.fetch, used)
            (work / "notes").mkdir(exist_ok=True)
            (work / "notes" / f"{n:02d}_{slug(title)}.md").write_text(md, encoding="utf-8")
            texts.append((f"plaud-{item['data_type']}", md))

        # Infografías que ninguna nota enlaza: se guardan igualmente.
        for ref, url in mapping.items():
            if ref not in used:
                used[ref] = save_asset(ref, url, work / "assets", self.client.fetch, used)
        return texts

    def _write_audio(self, fid: str, detail: dict, work: Path,
                     texts: list[tuple[str, str]]) -> str | None:
        if detail.get("audio_deleted"):
            return None
        url = self.client.audio_url(fid)
        if not url:
            return None
        audio = self.client.fetch(url)

        # Plaud no la transcribió: opcionalmente lo hacemos en local con whisper.
        if not texts and self.transcriber is not None:
            tmp = work / "audio.mp3"
            tmp.write_bytes(audio)
            result = self.transcriber.transcribe(tmp)
            (work / "transcript.md").write_text(result.text + "\n", encoding="utf-8")
            texts.append((result.model, result.text))
            tmp.unlink()

        if self.key is None:
            (work / "audio.mp3").write_bytes(audio)
            return "audio.mp3"
        (work / "audio.pv").write_bytes(encrypt_bytes(self.key, audio))
        return "audio.pv"


def _is_ours(folder: Path, fid: str) -> bool:
    marker = folder / MARKER
    return marker.exists() and marker.read_text().strip() == fid
