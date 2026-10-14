"""pipeline.py — El director de orquesta.

Aquí NO hay lógica de negocio nueva: este módulo solo coordina a los
especialistas (transcriber, db, crypto, notifier) en el orden correcto.
Patrón clásico de orquestación — cada módulo sabe hacer UNA cosa bien,
y el pipeline define la receta:

    1. sha256 del fichero → ¿duplicado? → fuera
    2. transcribir (o passthrough si es texto)
    3. registrar en SQLite (recordings + transcripts → FTS5 vía triggers)
    4. si es audio y crypto está activo: cifrar a archive/ y BORRAR original
    5. si es texto: mover a archive/ en claro (ya está indexado)
    6. notificar por Telegram

El orden del paso 3 antes del 4 es deliberado: primero aseguramos que la
transcripción está persistida; solo entonces destruimos el original en
claro. Si el proceso muere entre medias, lo peor que pasa es que queda
un original sin cifrar en inbox — nunca pierdes datos.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from .config import Config
from .crypto import encrypt_file
from .db import VaultDB
from .notifier import TelegramNotifier
from .transcriber import AUDIO_EXTENSIONS, TEXT_EXTENSIONS, Transcriber

logger = logging.getLogger("plaudvault.pipeline")

SUPPORTED = AUDIO_EXTENSIONS | TEXT_EXTENSIONS


def sha256_of(path: Path) -> str:
    """Huella digital del fichero. Se lee a trozos de 1 MiB para no cargar
    un audio de 2 horas entero en RAM."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Pipeline:
    def __init__(
        self,
        config: Config,
        db: VaultDB,
        transcriber: Transcriber,
        notifier: TelegramNotifier,
        master_key: bytes | None,
    ):
        self.cfg = config
        self.db = db
        self.transcriber = transcriber
        self.notifier = notifier
        self.master_key = master_key  # None si crypto.enabled = false

    def process_file(self, path: Path) -> int | None:
        """Procesa UN fichero del inbox. Devuelve el recording_id, o None
        si se ignoró (duplicado / extensión no soportada)."""
        ext = path.suffix.lower()
        if ext not in SUPPORTED:
            logger.info("Ignorado (extensión no soportada): %s", path.name)
            return None

        digest = sha256_of(path)
        existing = self.db.get_by_sha256(digest)
        if existing is not None and self._is_archived(existing):
            logger.info("Ignorado (duplicado): %s", path.name)
            path.unlink()  # ya está a salvo en el vault; la copia sobra
            return None

        source_type = "audio" if ext in AUDIO_EXTENSIONS else "text"
        if existing is not None:
            # Un run anterior registró el fichero pero murió antes de
            # archivarlo. Borrarlo aquí como "duplicado" perdería el único
            # original: en su lugar, terminamos el trabajo pendiente.
            rec_id = existing["id"]
            source_type = existing["source_type"]
            logger.warning("Reanudando #%d (quedó a medias): %s", rec_id, path.name)
            if self.db.get_transcript(rec_id) is None:
                result = self.transcriber.transcribe(path)
                self.db.add_transcript(rec_id, result.text, result.language, result.model)
        else:
            # --- 1. Transcribir ---
            logger.info("Procesando: %s", path.name)
            result = self.transcriber.transcribe(path)

            # --- 2. Persistir ANTES de tocar el original ---
            rec_id = self.db.add_recording(
                filename=path.name,
                sha256=digest,
                source_type=source_type,
                duration_secs=result.duration_secs,
            )
            self.db.add_transcript(rec_id, result.text, result.language, result.model)

        # --- 3. Archivar ---
        self.cfg.paths.archive.mkdir(parents=True, exist_ok=True)
        if source_type == "audio" and self.master_key is not None:
            dst = self.cfg.paths.archive / f"{rec_id:06d}_{path.stem}.pv"
            encrypt_file(self.master_key, path, dst)
            self.db.set_encrypted_path(rec_id, str(dst))
            path.unlink()  # el original en claro desaparece del disco
            logger.info("Audio cifrado → %s", dst.name)
        else:
            # Texto (o crypto desactivado): se archiva tal cual.
            dst = self.cfg.paths.archive / f"{rec_id:06d}_{path.name}"
            shutil.move(str(path), dst)

        # --- 4. Avisar ---
        preview = self.db.get_transcript(rec_id)["content"][:120].replace("\n", " ")
        self.notifier.notify(f"✅ PlaudVault · #{rec_id} «{path.name}»\n{preview}…")
        logger.info("Completado: #%d %s", rec_id, path.name)
        return rec_id

    def _is_archived(self, rec) -> bool:
        """¿Llegó esta grabación a guardarse en archive/? Solo entonces es
        seguro borrar una copia del inbox."""
        if rec["source_type"] == "audio" and self.master_key is not None:
            # Solo vale si la DB apunta a un .pv que existe: un .pv sin
            # registrar (murió entre cifrar y set_encrypted_path) se rehace.
            return bool(rec["encrypted_path"]) and Path(rec["encrypted_path"]).exists()
        # Texto o crypto desactivado: se movió como archive/{id:06d}_<nombre>.
        # (.tmp excluido: es un cifrado a medias, no un archivo válido.)
        return any(not p.name.endswith(".tmp")
                   for p in self.cfg.paths.archive.glob(f"{rec['id']:06d}_*"))

    def process_inbox(self) -> int:
        """Barre el inbox entero (útil al arrancar: procesa lo acumulado)."""
        count = 0
        for f in sorted(self.cfg.paths.inbox.iterdir()):
            if f.is_file() and self.process_file(f) is not None:
                count += 1
        return count
