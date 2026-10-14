"""reclassify: archive/MM-YY/<rec>/ → archive/<Carpeta>/MM-YY/<rec>/.

Forma de los datos según la sonda del 2026-10-14: GET /filetag/ →
data_filetag_list[{id, name, icon, color}] y filetag_id_list en cada grabación
(0 o 1 id; nunca varios).

Ejecutar:  .venv/bin/python tests/test_reclassify.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_sync import FakeClient, make_cfg  # noqa: E402

from plaudvault.crypto import decrypt_bytes, derive_key  # noqa: E402
from plaudvault.db import VaultDB  # noqa: E402
from plaudvault.reclassify import (  # noqa: E402
    SyncInProgress, apply, assert_no_sync_running, plan,
)
from plaudvault.sync import PlaudSync  # noqa: E402


class FolderClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.files[0]["filetag_id_list"] = ["t1"]   # octubre → "Trabajo/Ideas"
        self.files[1]["filetag_id_list"] = []       # agosto → Sin carpeta

    def folders(self):
        return {"t1": "Trabajo/Ideas"}  # con "/": debe sanearse


def imported(tmp: Path):
    cfg = make_cfg(tmp)
    db = VaultDB(cfg.paths.db)
    db.init_schema()
    key = derive_key("passphrase-de-test", b"0" * 16)
    assert PlaudSync(cfg, db, FolderClient(), key).run() == (2, 0, 0)
    return cfg, db, key


def test_reclassify_moves_and_updates_db():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, key = imported(Path(tmp))
        arc = cfg.paths.archive

        moves = plan(arc, db, FolderClient())
        assert apply(moves, db) == (2, 0)

        oct_dir = arc / "Trabajo-Ideas" / "10-26" / "2026-10-14_2049_Reunión- equipo-plan"
        aug_dir = arc / "Sin carpeta" / "08-25" / "2025-08-03_0905_Sin transcribir"
        assert (oct_dir / "notes" / "01_Resumen.md").exists()
        assert (aug_dir / "audio.pv").exists()
        assert not (arc / "10-26").exists() and not (arc / "08-25").exists(), "MM-YY vacíos borrados"

        # La DB apunta al sitio nuevo: decrypt sigue funcionando.
        rec = db.get_recording(1)
        assert rec["category"] == "Trabajo/Ideas"
        assert decrypt_bytes(key, Path(rec["encrypted_path"]).read_bytes()) == b"MP3-OCTUBRE"
        assert [r["category"] for r in db.list_categories()] == ["Sin carpeta", "Trabajo/Ideas"]
        assert len(db.list_recordings(category="Sin carpeta")) == 1

        # Idempotente.
        assert apply(plan(arc, db, FolderClient()), db) == (0, 2)
        db.close()


def test_recovers_from_crash_between_move_and_db():
    """Se movió la carpeta pero la DB quedó con la ruta vieja."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, _ = imported(Path(tmp))
        old_path = db.get_recording(1)["encrypted_path"]
        moves = plan(cfg.paths.archive, db, FolderClient())
        moves[0].dst.parent.mkdir(parents=True)
        moves[0].src.rename(moves[0].dst)          # «se cortó» aquí
        assert db.get_recording(1)["encrypted_path"] == old_path

        apply(plan(cfg.paths.archive, db, FolderClient()), db)
        assert Path(db.get_recording(1)["encrypted_path"]).exists()
        db.close()


def test_refuses_while_sync_running():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, _ = imported(Path(tmp))
        (cfg.paths.archive / "10-26" / "x.partial").mkdir()
        try:
            assert_no_sync_running(cfg.paths.archive)
            raise AssertionError("debió negarse")
        except SyncInProgress:
            pass
        db.close()


def test_name_collision_does_not_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, _ = imported(Path(tmp))
        moves = plan(cfg.paths.archive, db, FolderClient())
        intruder = moves[0].dst
        intruder.mkdir(parents=True)
        (intruder / "mio.txt").write_text("no me borres")
        apply(moves, db)
        assert (intruder / "mio.txt").read_text() == "no me borres"
        assert Path(db.get_recording(1)["encrypted_path"]).exists()
        db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
