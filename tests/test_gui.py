"""Tests de la GUI web (solo lectura): seguridad, .partial, Markdown, búsqueda y audio.

    .venv-gui/bin/python tests/test_gui.py

Monta un vault de prueba con tests/make_gui_fixture.py (PlaudSync real, mp3
real de 6 s, passphrase "test") en un directorio temporal.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from make_gui_fixture import PASSPHRASE, build  # noqa: E402
from plaudvault.config import load_config  # noqa: E402
from plaudvault.crypto import decrypt_bytes, unlock  # noqa: E402
from plaudvault.gui import create_app, fts_query, scan  # noqa: E402


def logged_client(base: Path):
    cfg = load_config(base / "config.yaml")
    app = create_app(cfg, "tok", {"testserver"})
    c = TestClient(app)
    assert c.get("/api/recordings").status_code == 401           # sin cookie: fuera
    assert c.get("/login?t=mal").status_code == 401
    assert c.get("/login?t=tok", follow_redirects=False).status_code == 303
    assert TestClient(app).get("/login?t=tok").status_code == 401  # token de un solo uso
    return cfg, c


def test_everything():
    with tempfile.TemporaryDirectory() as tmp:
        base = build(Path(tmp), minutes=0.1)
        cfg, c = logged_client(base)

        # DNS rebinding: otro Host → rechazado aunque haya cookie.
        assert c.get("/api/recordings", headers={"host": "evil.example:80"}).status_code == 400

        # Lista: 3 grabaciones, el .partial no aparece, todas indexadas.
        recs = c.get("/api/recordings").json()["recordings"]
        assert len(recs) == 3 and all(r["indexed"] for r in recs), recs
        assert not any("partial" in r["key"] for r in recs)
        assert len(scan(cfg.paths.archive)) == 3
        key = next(r["key"] for r in recs if "Reunión" in r["key"])

        # Documentos en orden y Markdown seguro.
        docs = c.get(f"/api/rec/{key}").json()["docs"]
        assert docs == ["transcript.md", "outline.md", "notes/01_Resumen.md"], docs
        html = c.get(f"/api/doc/{key}", params={"name": "notes/01_Resumen.md"}).json()["html"]
        assert "<script>" not in html and "&lt;script&gt;" in html, html
        assert "<table>" in html
        assert 'src="/files/10-26/' in html and "assets/infografia.png" in html, html
        assert "example.com" in html and 'src="https://' not in html  # remota → texto
        r = c.get(f"/api/doc/{key}", params={"name": "../../vault.salt"})
        assert r.status_code == 404

        # Ficheros: solo imágenes de grabaciones publicadas, con CSP.
        img = html.split('src="')[1].split('"')[0]
        r = c.get(img)
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"  # infografías fuera de la caché de disco
        for bad in ("/files/../vault.salt", "/files/%2e%2e/vault.salt",
                    f"/files/{key}/audio.pv", "/files/10-26/x.partial/a.png"):
            assert c.get(bad).status_code == 404, bad
        assert c.get("/api/rec/10-26/2026-10-31_2359_A medio importar.partial").status_code == 404
        assert c.get("/api/rec/..").status_code == 404

        # Búsqueda FTS5 (DB en mode=ro) y entrada rara sin 500.
        res = c.get("/api/search", params={"q": "presupuesto"}).json()["results"]
        assert any(x["key"] == key and "\x02" in x["frag"] for x in res), res
        assert c.get("/api/search", params={"q": 'a" OR -b ('}).status_code == 200
        assert fts_query('di "hola"') == '"di" """hola"""'

        # Audio: bloqueado → 401; passphrase mala → 401; buena → bytes descifrados.
        assert c.get(f"/api/audio/{key}").status_code == 401
        assert c.post("/api/unlock", json={"passphrase": "nope"}).status_code == 401
        assert c.post("/api/unlock", json={"passphrase": PASSPHRASE}).status_code == 200
        k = unlock(cfg.salt_path, cfg.keycheck_path, PASSPHRASE)
        mp3 = decrypt_bytes(k, (cfg.paths.archive / key / "audio.pv").read_bytes())
        full = c.get(f"/api/audio/{key}")
        assert full.status_code == 200 and full.content == mp3
        assert full.headers["cache-control"] == "no-store"
        part = c.get(f"/api/audio/{key}", headers={"range": "bytes=0-99"})
        assert part.status_code == 206 and part.content == mp3[:100]
        assert part.headers["content-range"] == f"bytes 0-99/{len(mp3)}"
        assert c.get(f"/api/audio/{key}", headers={"range": "bytes=-10"}).content == mp3[-10:]
        assert c.get(f"/api/audio/{key}", headers={"range": f"bytes={len(mp3)}-"}).status_code == 416

        # Bloquear suelta la clave.
        c.post("/api/lock")
        assert c.get(f"/api/audio/{key}").status_code == 401
        # La GUI no escribió nada: sin -journal, y el -wal (si existe) vacío.
        assert not (base / "vault.db-journal").exists()
        wal = base / "vault.db-wal"
        assert not wal.exists() or wal.stat().st_size == 0
    print("✅ test_everything")


if __name__ == "__main__":
    test_everything()
