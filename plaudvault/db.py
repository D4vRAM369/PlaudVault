"""db.py — Almacenamiento e índice de búsqueda (SQLite + FTS5).

Por qué SQLite y no JSON/ficheros: necesitas buscar dentro de cientos de
transcripciones en milisegundos. FTS5 (Full-Text Search v5) es un índice
invertido integrado en SQLite: como el índice analítico al final de un
libro, en vez de leer el libro entero busca la palabra en el índice y
salta directo a las páginas. Cero servidores, un solo fichero .db.

Decisión de diseño (ya fijada en el spec): las transcripciones se guardan
EN CLARO para que FTS5 pueda indexarlas. Lo que se cifra es el audio.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Esquema completo. Se ejecuta en `init` y es idempotente (IF NOT EXISTS):
# puedes lanzarlo mil veces sin romper nada.
SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    sha256          TEXT NOT NULL UNIQUE,   -- huella del fichero: evita duplicados
    source_type     TEXT NOT NULL,          -- 'audio' | 'text'
    duration_secs   REAL,                   -- NULL para fuentes de texto
    encrypted_path  TEXT,                   -- ruta al .pv en archive/ (solo audio)
    status          TEXT NOT NULL DEFAULT 'processed',
    ingested_at     TEXT NOT NULL,
    plaud_file_id   TEXT                    -- id en Plaud (solo sync); UNIQUE vía índice
);

CREATE TABLE IF NOT EXISTS transcripts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id    INTEGER NOT NULL,
    content         TEXT NOT NULL,
    language        TEXT,
    model           TEXT,                   -- 'faster-whisper-small' | 'passthrough'
    created_at      TEXT NOT NULL,
    FOREIGN KEY (recording_id) REFERENCES recordings(id)
);

-- Tabla virtual FTS5 espejo de transcripts.content.
-- content='transcripts' = FTS5 no duplica el texto, solo indexa (ahorra espacio).
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    content,
    content='transcripts',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- Triggers: mantienen el índice FTS sincronizado automáticamente.
-- Como un bibliotecario que actualiza el índice cada vez que entra
-- o sale un libro — tú nunca lo gestionas a mano.
CREATE TRIGGER IF NOT EXISTS transcripts_ai AFTER INSERT ON transcripts BEGIN
    INSERT INTO transcripts_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_ad AFTER DELETE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_au AFTER UPDATE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
    INSERT INTO transcripts_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def _now() -> str:
    """Timestamp ISO-8601 en UTC. Siempre UTC en la DB; la zona horaria
    es un problema de presentación, no de almacenamiento."""
    return datetime.now(timezone.utc).isoformat()


class VaultDB:
    """Capa de acceso a datos. Todo el SQL del proyecto vive aquí.

    Por qué una clase y no funciones sueltas: la conexión se abre una vez
    y se reutiliza, y el resto del código habla en términos del dominio
    (add_recording, search) en vez de SQL crudo.
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row  # filas accesibles por nombre
        self.conn.execute("PRAGMA foreign_keys = ON")

    def init_schema(self) -> None:
        # WAL: lectores (la GUI) y escritor (sync) no se bloquean entre sí.
        # Es persistente en el fichero; en una DB que ya es WAL no hace nada.
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        # Migración: vaults creados antes del sync no tienen la columna.
        # CREATE TABLE IF NOT EXISTS no altera tablas ya existentes.
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(recordings)")}
        if "category" not in cols:
            # Carpeta de Plaud ("Sin carpeta" si no tiene). La rellena `reclassify`.
            self.conn.execute("ALTER TABLE recordings ADD COLUMN category TEXT")
        if "plaud_file_id" not in cols:
            # SQLite no permite ADD COLUMN ... UNIQUE: la unicidad va en un índice.
            self.conn.execute("ALTER TABLE recordings ADD COLUMN plaud_file_id TEXT")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_recordings_plaud "
            "ON recordings(plaud_file_id)"
        )
        self.conn.commit()

    # ---------- escritura ----------

    def get_by_sha256(self, sha256: str) -> sqlite3.Row | None:
        cur = self.conn.execute(
            "SELECT * FROM recordings WHERE sha256 = ?", (sha256,)
        )
        return cur.fetchone()

    def has_plaud_file(self, plaud_file_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM recordings WHERE plaud_file_id = ?", (plaud_file_id,)
        )
        return cur.fetchone() is not None

    def add_recording(
        self,
        filename: str,
        sha256: str,
        source_type: str,
        duration_secs: float | None = None,
        encrypted_path: str | None = None,
        plaud_file_id: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO recordings
               (filename, sha256, source_type, duration_secs, encrypted_path,
                ingested_at, plaud_file_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (filename, sha256, source_type, duration_secs, encrypted_path, _now(),
             plaud_file_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_transcript(
        self, recording_id: int, content: str, language: str, model: str
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO transcripts (recording_id, content, language, model, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (recording_id, content, language, model, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_encrypted_path(self, recording_id: int, encrypted_path: str) -> None:
        self.conn.execute(
            "UPDATE recordings SET encrypted_path = ? WHERE id = ?",
            (encrypted_path, recording_id),
        )
        self.conn.commit()

    # ---------- lectura ----------

    def search(self, query: str, limit: int = 10) -> list[sqlite3.Row]:
        """Búsqueda full-text. snippet() devuelve el fragmento donde aparece
        el término, con marcadores >>> <<< alrededor de la coincidencia."""
        return self.conn.execute(
            """SELECT r.id, r.filename, r.ingested_at, r.source_type,
                      snippet(transcripts_fts, 0, '>>>', '<<<', ' … ', 12) AS frag
               FROM transcripts_fts
               JOIN transcripts t ON t.id = transcripts_fts.rowid
               JOIN recordings r ON r.id = t.recording_id
               WHERE transcripts_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()

    def get_recording(self, recording_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM recordings WHERE id = ?", (recording_id,)
        ).fetchone()

    def get_transcript(self, recording_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT r.id, r.filename, r.source_type, r.ingested_at,
                      t.content, t.language, t.model
               FROM recordings r
               JOIN transcripts t ON t.recording_id = r.id
               WHERE r.id = ?""",
            (recording_id,),
        ).fetchone()

    def list_recordings(self, limit: int = 20,
                        category: str | None = None) -> list[sqlite3.Row]:
        # SELECT *: en un vault aún sin migrar no existe `category` y la fila
        # simplemente no la trae (sin filtro, no falla).
        sql = "SELECT * FROM recordings"
        args: tuple = ()
        if category is not None:
            sql += " WHERE category = ?"
            args = (category,)
        return self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()

    def list_categories(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT category, COUNT(*) AS n FROM recordings "
            "WHERE category IS NOT NULL GROUP BY category ORDER BY category"
        ).fetchall()

    def plaud_recordings(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, plaud_file_id, encrypted_path, category FROM recordings "
            "WHERE plaud_file_id IS NOT NULL"
        ).fetchall()

    def set_location(self, recording_id: int, encrypted_path: str | None,
                     category: str) -> None:
        self.conn.execute(
            "UPDATE recordings SET encrypted_path = ?, category = ? WHERE id = ?",
            (encrypted_path, category, recording_id),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
