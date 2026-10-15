"""Regresión: texto libre con signos no debe interpretarse como sintaxis FTS5."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plaudvault.db import VaultDB


def test_search_accepts_plain_text():
    with tempfile.TemporaryDirectory() as tmp:
        db = VaultDB(Path(tmp) / "vault.db")
        try:
            db.init_schema()
            rec_id = db.add_recording("ejemplo.txt", "test-search", "text")
            db.add_transcript(rec_id, "Reunión de seguimiento-presupuesto: di hola", "es", "test")
            for query in ("reunion", "seguimiento-presupuesto", 'di "hola"', "presupuesto:"):
                rows = db.search(query)
                assert [row["id"] for row in rows] == [rec_id], query
            for query in ('"', "-", ":", "", "   ", "inexistente"):
                assert db.search(query) == [], query
        finally:
            db.close()
    print("✅ test_search_accepts_plain_text")


if __name__ == "__main__":
    test_search_accepts_plain_text()
