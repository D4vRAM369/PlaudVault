"""Regresión: un fallo al archivar NO debe hacer que el siguiente run borre el
original como "duplicado" (pérdida de datos).

Ejecutar:  .venv/bin/python tests/test_pipeline.py
"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plaudvault import pipeline as pl  # noqa: E402
from plaudvault.config import Config, VaultPaths  # noqa: E402
from plaudvault.crypto import decrypt_bytes, derive_key  # noqa: E402
from plaudvault.db import VaultDB  # noqa: E402
from plaudvault.transcriber import TranscriptionResult  # noqa: E402


class FakeTranscriber:
    calls = 0

    def transcribe(self, path):
        FakeTranscriber.calls += 1
        return TranscriptionResult("hola mundo", "es", "fake", 1.0)


class NullNotifier:
    def notify(self, _msg):
        pass


def setup(tmp: Path, key):
    cfg = Config(paths=VaultPaths(base=tmp, db=tmp / "vault.db",
                                  inbox=tmp / "inbox", archive=tmp / "archive"))
    cfg.paths.inbox.mkdir(parents=True)
    db = VaultDB(cfg.paths.db)
    db.init_schema()
    return cfg, db, pl.Pipeline(cfg, db, FakeTranscriber(), NullNotifier(), key)


def test_failed_encrypt_is_resumed_not_deleted():
    key = derive_key("passphrase-de-test", b"0" * 16)
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, pipe = setup(Path(tmp), key)
        audio = cfg.paths.inbox / "nota.mp3"
        audio.write_bytes(b"MP3-UNICO")

        # 1er run: el disco se llena justo al cifrar.
        with mock.patch.object(pl, "encrypt_file", side_effect=OSError("disco lleno")):
            try:
                pipe.process_file(audio)
            except OSError:
                pass
        assert audio.exists(), "el original debe seguir en el inbox"

        # 2º run: antes del arreglo, esto BORRABA el audio sin cifrarlo nunca.
        FakeTranscriber.calls = 0
        rec_id = pipe.process_file(audio)
        assert rec_id == 1, "debe reanudar la MISMA grabación, no crear otra"
        assert FakeTranscriber.calls == 0, "no hace falta re-transcribir"
        assert not audio.exists()
        pv = Path(db.get_recording(1)["encrypted_path"])
        assert decrypt_bytes(key, pv.read_bytes()) == b"MP3-UNICO"

        # 3er run con una copia: ahora sí es duplicado real → se descarta.
        audio.write_bytes(b"MP3-UNICO")
        assert pipe.process_file(audio) is None and not audio.exists()
        db.close()


def test_missing_transcript_is_redone():
    """Fallo entre add_recording y add_transcript: grabación sin transcript."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg, db, pipe = setup(Path(tmp), None)
        note = cfg.paths.inbox / "n.txt"
        note.write_text("texto")
        with mock.patch.object(db, "add_transcript", side_effect=OSError("boom")):
            try:
                pipe.process_file(note)
            except OSError:
                pass
        assert pipe.process_file(note) == 1
        assert db.get_transcript(1) is not None
        assert (cfg.paths.archive / "000001_n.txt").exists()
        db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✅ {name}")
