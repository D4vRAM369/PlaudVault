"""Test de extremo a extremo del sync con un cliente Plaud FALSO.

Los datos imitan la forma real que devolvió la API (sonda del 2026-10-14):
content_list con data_type/task_status/data_link, imágenes del Markdown
resueltas vía download_path_mapping, y audio vía temp-url.

Ejecutar:  .venv/bin/python tests/test_sync.py
"""
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plaudvault.config import Config, VaultPaths  # noqa: E402
from plaudvault.crypto import decrypt_bytes, derive_key  # noqa: E402
from plaudvault.db import VaultDB  # noqa: E402
from plaudvault.sync import PlaudSync, fmt_ts, month_dir, slug  # noqa: E402

OCT = int(datetime(2026, 10, 14, 20, 49).timestamp() * 1000)
AUG = int(datetime(2025, 8, 3, 9, 5).timestamp() * 1000)
IMG_REF = "permanent/abc/summary@infografia.png"

BLOBS = {  # url → contenido, como S3
    "s3://trans": json.dumps([
        {"start_time": 0, "end_time": 5000, "content": "Hola soberanía", "speaker": "Brian"},
        {"start_time": 65000, "end_time": 70000, "content": "Adiós", "speaker": "Ana"},
    ]),
    "s3://outline": json.dumps([{"start_time": 0, "end_time": 9, "topic": "Intro"}]),
    "s3://note1": f"# Resumen\n\n![infografía]({IMG_REF})\n\nTexto clave: murciélago\n",
    "s3://note2": "# Preguntas\n\n![web](https://example.com/foto.png?x=1)\n",
    "s3://memo": json.dumps([{"time": 1000, "text": "marca"}]),
    "s3://img": b"PNG-INFOGRAFIA",
    "s3://orphan": b"PNG-HUERFANA",
    "https://example.com/foto.png?x=1": b"PNG-WEB",
    "s3://audio-oct": b"MP3-OCTUBRE",
    "s3://audio-aug": b"MP3-AGOSTO",
}


class FakeClient:
    def __init__(self):
        self.files = [
            {"id": "f" * 32, "filename": "Reunión: equipo/plan", "start_time": OCT, "duration": 70000},
            {"id": "a" * 32, "filename": "Sin transcribir", "start_time": AUG, "duration": 1000},
        ]
        self.details = {
            "f" * 32: {
                "content_list": [
                    {"data_id": "1", "data_type": "transaction", "task_status": 1, "data_link": "s3://trans"},
                    {"data_id": "2", "data_type": "outline", "task_status": 1, "data_link": "s3://outline"},
                    {"data_id": "3", "data_type": "auto_sum_note", "task_status": 1, "data_title": "Resumen", "data_link": "s3://note1"},
                    {"data_id": "4", "data_type": "ask_note", "task_status": 1, "data_title": "¿Qué es X?", "data_link": "s3://note2"},
                    {"data_id": "5", "data_type": "mark_memo", "task_status": 1, "data_title": "Marcas", "data_link": "s3://memo"},
                    {"data_id": "6", "data_type": "high_light", "task_status": -110, "data_link": "s3://roto"},
                ],
                "download_path_mapping": {IMG_REF: "s3://img", "permanent/abc/extra.png": "s3://orphan"},
                "pre_download_content_list": [],
            },
            "a" * 32: {"content_list": []},  # Plaud nunca la transcribió
        }

    def iter_files(self):
        yield from self.files

    def file_detail(self, fid):
        return self.details[fid]

    def audio_url(self, fid):
        return "s3://audio-oct" if fid.startswith("f") else "s3://audio-aug"

    @staticmethod
    def fetch(url):
        data = BLOBS[url]
        return data if isinstance(data, bytes) else data.encode()

    @classmethod
    def fetch_json_or_text(cls, url):
        text = cls.fetch(url).decode()
        try:
            return json.loads(text)
        except ValueError:
            return text


def make_cfg(base: Path) -> Config:
    return Config(paths=VaultPaths(base=base, db=base / "vault.db",
                                   inbox=base / "inbox", archive=base / "archive"))


def test_pure_helpers():
    assert month_dir(OCT) == "10-26" and month_dir(AUG) == "08-25"
    assert fmt_ts(65000) == "01:05" and fmt_ts(3_725_000) == "1:02:05"
    assert slug('a/b:c*?"d') == "a-b-c-d"
    assert slug("...") == "sin_titulo"


def test_full_sync():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cfg = make_cfg(base)

        # Vault "antiguo" sin la columna plaud_file_id → la migración la añade.
        old = sqlite3.connect(base / "vault.db")
        old.execute("CREATE TABLE recordings (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "filename TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL, "
                    "duration_secs REAL, encrypted_path TEXT, status TEXT NOT NULL DEFAULT 'processed', "
                    "ingested_at TEXT NOT NULL)")
        old.close()

        db = VaultDB(cfg.paths.db)
        db.init_schema()
        key = derive_key("passphrase-de-test", b"0" * 16)

        # Resto de un sync interrumpido: debe limpiarse, no publicarse.
        stale = cfg.paths.archive / "10-26" / "2026-10-14_2049_Reunión- equipo-plan.partial"
        stale.mkdir(parents=True)
        (stale / "basura.txt").write_text("x")

        assert PlaudSync(cfg, db, FakeClient(), key).run() == (2, 0, 0)

        rec = cfg.paths.archive / "10-26" / "2026-10-14_2049_Reunión- equipo-plan"
        assert rec.is_dir(), sorted(p.name for p in (cfg.paths.archive / "10-26").iterdir())
        assert not stale.exists()
        assert (cfg.paths.archive / "08-25" / "2025-08-03_0905_Sin transcribir" / "audio.pv").exists()

        # Transcript y outline legibles, con timestamps y speakers.
        t = (rec / "transcript.md").read_text()
        assert "**[00:00] Brian:** Hola soberanía" in t and "**[01:05] Ana:** Adiós" in t
        assert "- [00:00] Intro" in (rec / "outline.md").read_text()

        # TODAS las notas, numeradas y con título; la fallida (-110) se omite.
        notes = sorted(p.name for p in (rec / "notes").iterdir())
        assert notes == ["01_Resumen.md", "02_¿Qué es X.md", "03_Marcas.md"], notes
        assert "```json" in (rec / "notes" / "03_Marcas.md").read_text()

        # Infografías: enlazada, huérfana y web, todas locales y reescritas.
        n1 = (rec / "notes" / "01_Resumen.md").read_text()
        assert "![infografía](../assets/infografia.png)" in n1, n1
        assert "![web](../assets/foto.png)" in (rec / "notes" / "02_¿Qué es X.md").read_text()
        assert (rec / "assets" / "infografia.png").read_bytes() == b"PNG-INFOGRAFIA"
        assert (rec / "assets" / "extra.png").read_bytes() == b"PNG-HUERFANA"
        assert (rec / "assets" / "foto.png").read_bytes() == b"PNG-WEB"

        # Audio cifrado y recuperable; nunca en claro en disco.
        assert not list(base.rglob("*.mp3"))
        assert decrypt_bytes(key, (rec / "audio.pv").read_bytes()) == b"MP3-OCTUBRE"

        # Búsqueda: encuentra texto de las notas, no solo del transcript.
        assert db.search("murcielago"), "FTS debe ignorar tildes y buscar en notas"
        r = db.get_recording(1)
        assert r["plaud_file_id"] == "f" * 32 and r["filename"].endswith(".mp3")

        # Idempotente: un segundo sync no duplica nada.
        assert PlaudSync(cfg, db, FakeClient(), key).run() == (0, 2, 0)
        db.close()


def test_no_crypto_keeps_mp3():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(Path(tmp))
        db = VaultDB(cfg.paths.db)
        db.init_schema()
        assert PlaudSync(cfg, db, FakeClient(), None).run(limit=1) == (1, 0, 0)
        assert len(list(Path(tmp).rglob("audio.mp3"))) == 1
        db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
