"""reclassify.py — Reordena lo importado por carpetas de Plaud.

    archive/MM-YY/<grabación>/   →   archive/<Carpeta Plaud>/MM-YY/<grabación>/

Se lanza DESPUÉS de un sync (nunca a la vez: ver `assert_no_sync_running`).

Cómo se mantiene a salvo:
- Cada grabación se localiza por su marca `.plaud_id`, no por su ruta: da igual
  que la hayas movido a mano o que una ejecución anterior se cortara a mitad.
- Primero se mueve la carpeta y luego se actualiza la DB. Si se corta entre
  ambos pasos, la siguiente ejecución encuentra la carpeta en su sitio nuevo
  (por la marca) y corrige la DB. Por eso es seguro repetirlo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .db import VaultDB
from .plaud_client import PlaudClient
from .sync import MARKER, month_dir, slug

logger = logging.getLogger("plaudvault.reclassify")

NO_FOLDER = "Sin carpeta"


class SyncInProgress(Exception):
    """Hay carpetas .partial: un sync está corriendo o se cortó."""


@dataclass
class Move:
    rec_id: int
    src: Path
    dst: Path
    category: str


def assert_no_sync_running(archive: Path) -> None:
    partial = next(archive.rglob("*.partial"), None) if archive.exists() else None
    if partial is not None:
        raise SyncInProgress(
            f"Hay una importación en curso o cortada ({partial.name}). "
            "Espera a que termine `sync` (o relánzalo para que limpie) y repite."
        )


def find_folders(archive: Path) -> dict[str, Path]:
    """{plaud_file_id: carpeta} leyendo las marcas .plaud_id del disco."""
    found = {}
    for marker in archive.rglob(MARKER):
        folder = marker.parent
        if folder.name.endswith(".partial"):
            continue
        found[marker.read_text().strip()] = folder
    return found


def plan(archive: Path, db: VaultDB, client: PlaudClient) -> list[Move]:
    names = client.folders()
    remote = {}  # file_id → (categoría, start_time)
    for f in client.iter_files():
        tags = [str(t) for t in f.get("filetag_id_list") or []]
        # ponytail: primera carpeta; la sonda vio 0 grabaciones en varias.
        tag = tags[0] if tags else None
        if tag is not None and tag not in names:
            logger.warning("Carpeta %s sin nombre en Plaud: va a «%s»", tag, NO_FOLDER)
        remote[f["id"]] = (names.get(tag, NO_FOLDER) if tag else NO_FOLDER,
                           f["start_time"])

    on_disk = find_folders(archive)
    moves = []
    for rec in db.plaud_recordings():
        fid = rec["plaud_file_id"]
        src = on_disk.get(fid)
        if src is None:
            logger.warning("#%d: su carpeta no está en archive/ (¿borrada?)", rec["id"])
            continue
        if fid not in remote:
            logger.warning("#%d: ya no existe en Plaud; se deja donde está", rec["id"])
            continue
        category, start = remote[fid]
        dst = archive / slug(category) / month_dir(start) / src.name
        moves.append(Move(rec["id"], src, dst, category))
    return moves


def apply(moves: list[Move], db: VaultDB) -> tuple[int, int]:
    """Devuelve (movidas, ya en su sitio)."""
    moved = same = 0
    for mv in moves:
        dst = mv.dst
        if dst != mv.src:
            if dst.exists():  # otra grabación con el mismo nombre: no pisarla
                dst = dst.with_name(f"{dst.name}_{mv.rec_id}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            mv.src.rename(dst)
            _remove_empty_parents(mv.src.parent, stop=dst.parents[2])
            moved += 1
        else:
            same += 1
        audio = next((dst / n for n in ("audio.pv", "audio.mp3") if (dst / n).exists()), None)
        # Se reescribe siempre: así se repara una DB que quedó apuntando a la
        # ruta vieja si una ejecución anterior se cortó tras mover.
        db.set_location(mv.rec_id, str(audio) if audio else None, mv.category)
    return moved, same


def _remove_empty_parents(folder: Path, stop: Path) -> None:
    """Borra los MM-YY/ que quedan vacíos tras mover, sin pasar de archive/."""
    while folder != stop and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent
