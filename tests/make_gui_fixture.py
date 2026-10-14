"""make_gui_fixture.py — Vault de prueba para desarrollar la GUI sin tocar ~/PlaudVault.

Usa el PlaudSync REAL con un cliente falso, así las carpetas, la DB y los .pv
salen exactamente como en producción. A diferencia del FakeClient de
test_sync.py, aquí los bytes son de verdad: un PNG que el navegador puede
pintar y un mp3 (ruido, generado con ffmpeg) que puede reproducir.

    .venv/bin/python tests/make_gui_fixture.py /tmp/pv-gui [--minutes 12]

Passphrase del vault de prueba: "test" (valor de fixture, no es secreto).
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plaudvault.config import Config, VaultPaths  # noqa: E402
from plaudvault.crypto import init_vault_crypto, unlock  # noqa: E402
from plaudvault.db import VaultDB  # noqa: E402
from plaudvault.sync import PlaudSync  # noqa: E402

PASSPHRASE = "test"


def png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    """PNG mínimo válido con stdlib: firma + IHDR + IDAT (zlib) + IEND."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    row = b"\x00" + bytes(rgb) * w  # filtro 0 + píxeles
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * h))
            + chunk(b"IEND", b""))


def mp3(minutes: float) -> bytes:
    # Ruido rosa: cada trozo del fichero es único, así el spike puede buscarlo
    # byte a byte en disco sin falsos positivos.
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi",
         "-i", f"anoisesrc=color=pink:amplitude=0.1:duration={minutes * 60}",
         "-ac", "1", "-b:a", "128k", "-f", "mp3", "-"],
        check=True, capture_output=True).stdout


NOTE = """# Resumen de {title}

Texto con **negrita**, una lista y una tabla.

- Punto uno
- Punto dos

| Tema | Minuto |
|---|---|
| Intro | 00:00 |

![infografía](permanent/{fid}/summary@infografia.png)

![remota](https://example.com/tracker.png)

<script>alert("si ves esto, el HTML crudo no está desactivado")</script>
"""


class FixtureClient:
    """Misma interfaz que PlaudClient (iter_files, file_detail, audio_url…)."""

    def __init__(self, recs: list[tuple[str, int, str]], audio: bytes):
        self.recs, self.audio = recs, audio
        self.blobs: dict[str, object] = {}

    def iter_files(self):
        for fid, start, title in self.recs:
            yield {"id": fid, "filename": title, "start_time": start, "duration": 60_000}

    def file_detail(self, fid):
        title = next(t for f, _, t in self.recs if f == fid)
        self.blobs.update({
            f"s3://{fid}/trans": [{"start_time": 0, "speaker": "Ana", "content": f"Hola, esto es {title}. Hablamos del presupuesto."},
                                  {"start_time": 65_000, "speaker": "Luis", "content": "De acuerdo con la planificación."}],
            f"s3://{fid}/outline": [{"start_time": 0, "topic": "Presupuesto"}],
            f"s3://{fid}/note": NOTE.format(title=title, fid=fid),
            f"s3://{fid}/img": png(320, 120, (40, 120, 200)),
        })
        return {
            "content_list": [
                {"data_id": "1", "data_type": "transaction", "task_status": 1, "data_link": f"s3://{fid}/trans"},
                {"data_id": "2", "data_type": "outline", "task_status": 1, "data_link": f"s3://{fid}/outline"},
                {"data_id": "3", "data_type": "auto_sum_note", "task_status": 1, "data_title": "Resumen", "data_link": f"s3://{fid}/note"},
            ],
            "download_path_mapping": {f"permanent/{fid}/summary@infografia.png": f"s3://{fid}/img"},
        }

    def audio_url(self, fid):
        return "s3://audio"

    def fetch(self, url):
        if url == "s3://audio":
            return self.audio
        if url.startswith("https://"):
            raise ConnectionError("sin red en el fixture")  # la imagen remota queda como enlace
        return self.blobs[url]

    def fetch_json_or_text(self, url):
        return self.blobs[url]


def build(base: Path, minutes: float) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    cfg = Config(paths=VaultPaths(base=base, db=base / "vault.db",
                                  inbox=base / "inbox", archive=base / "archive"))
    for p in (cfg.paths.inbox, cfg.paths.archive):
        p.mkdir(exist_ok=True)
    if not cfg.salt_path.exists():
        init_vault_crypto(cfg.salt_path, cfg.keycheck_path, PASSPHRASE)
    key = unlock(cfg.salt_path, cfg.keycheck_path, PASSPHRASE)

    db = VaultDB(cfg.paths.db)
    db.init_schema()
    recs = [("fix-oct-1", 1_791_878_400_000, "Reunión de presupuesto"),   # 2026-10
            ("fix-oct-2", 1_791_964_800_000, "Llamada con Luis"),
            ("fix-ago-1", 1_786_000_000_000, "Ideas de agosto")]          # 2026-08
    done, skipped, failed = PlaudSync(cfg, db, FixtureClient(recs, mp3(minutes)), key).run()
    db.close()

    # Señuelo: un import a medias. La GUI NO debe mostrarlo.
    partial = cfg.paths.archive / "10-26" / "2026-10-31_2359_A medio importar.partial"
    partial.mkdir(parents=True, exist_ok=True)
    (partial / ".plaud_id").write_text("fix-partial")

    (base / "config.yaml").write_text(
        f"vault:\n  base_path: {base}\n  db_path: {base}/vault.db\n"
        f"  inbox_path: {base}/inbox\n  archive_path: {base}/archive\n"
        f"crypto:\n  enabled: true\nlogging:\n  file: {base}/plaudvault.log\n",
        encoding="utf-8")
    print(f"Fixture en {base}: importadas={done} ya_existían={skipped} fallidas={failed}")
    return base


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--minutes", type=float, default=12,
                    help="duración del mp3; >8 min ≈ >8 MB (umbral de caché de Firefox)")
    a = ap.parse_args()
    build(a.dir.resolve(), a.minutes)
