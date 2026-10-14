"""Regresión: un fallo a mitad de registrar una grabación no deja una fila
huérfana (grabación sin transcripts) que bloquee su reimportación.

    .venv/bin/python tests/test_sync_tx.py
"""

import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from plaudvault.crypto import derive_key  # noqa: E402
from plaudvault.db import VaultDB  # noqa: E402
from plaudvault.sync import PlaudSync  # noqa: E402
from test_sync import FakeClient, make_cfg  # noqa: E402


def test_failure_mid_transaction_rolls_back():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(Path(tmp))
        db = VaultDB(cfg.paths.db)
        db.init_schema()
        key = derive_key("passphrase-de-test", b"0" * 16)
        assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

        real = db.add_transcript
        calls = {"n": 0}

        def flaky(*a, **kw):
            # La 2ª nota falla, como si el commit hubiera chocado con un lector.
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3.OperationalError("database is locked")
            return real(*a, **kw)

        with mock.patch.object(db, "add_transcript", side_effect=flaky):
            done, skipped, failed = PlaudSync(cfg, db, FakeClient(), key).run()
        assert failed >= 1, (done, skipped, failed)
        assert not db.conn.in_transaction  # nada colgando que bloquee a otros

        # La que falló no quedó registrada a medias (antes: fila sin
        # transcripts que has_plaud_file() daba por importada para siempre).
        n = db.conn.execute("SELECT count(*) FROM recordings").fetchone()[0]
        assert n == done, (n, done)

        # El siguiente sync la reimporta (la carpeta es nuestra: se sustituye).
        assert PlaudSync(cfg, db, FakeClient(), key).run() == (failed, done, 0)
        assert db.conn.execute("SELECT count(*) FROM transcripts").fetchone()[0] > 0
        db.close()
    print("✅ test_failure_mid_transaction_rolls_back")


if __name__ == "__main__":
    test_failure_mid_transaction_rolls_back()
