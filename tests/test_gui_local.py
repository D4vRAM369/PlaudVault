"""Regresión de fuentes locales y frontend desktop, sin vault ni audio reales."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from plaudvault.config import Config, VaultPaths
from plaudvault.db import VaultDB
from plaudvault.gui import create_app


def test_local():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        archive = base / 'archive'
        archive.mkdir()
        cfg = Config(VaultPaths(base, base / 'vault.db', base / 'inbox', archive))
        db = VaultDB(cfg.paths.db)
        db.init_schema()
        def add(name, kind='text', path=None, archived=True):
            rid = db.add_recording(name, name, kind, encrypted_path=str(path) if path else None)
            db.add_transcript(rid, 'presupuesto <script>x</script> ![x](https://example.com/x.png)', 'es', 'local')
            if archived and not path:
                (archive / f'{rid:06d}_{name}').write_bytes(b'RIFFfakewave')
            return rid
        txt = add('nota.md')
        wav = add('voz.wav', 'audio')
        pv = archive / 'encrypted.pv'
        pv.write_bytes(b'encrypted')
        enc = add('voz.flac', 'audio', pv)
        missing = add('missing.md', archived=False)
        outside = base / 'outside.pv'
        outside.write_bytes(b'private')
        escaped = add('escape.mp3', 'audio', outside)
        link = archive / 'link.pv'
        link.symlink_to(outside)
        linked = add('link.mp3', 'audio', link)
        partial = archive / 'x.partial'
        partial.mkdir()
        pending = partial / 'audio.pv'
        pending.write_bytes(b'pending')
        incomplete = add('pending.mp3', 'audio', pending)
        db.close()
        front = base / 'frontend'
        front.mkdir()
        (front / 'index.html').write_text('desktop index')
        (front / 'asset.js').write_text('desktop asset')
        (front / 'escape.js').symlink_to(outside)
        app = create_app(cfg, 'tok', {'testserver'}, frontend_dir=front)
        with TestClient(app) as c:
            assert c.get('/desktop/asset.js').status_code == 401
            c.get('/login?t=tok', follow_redirects=False)
            assert c.get('/').text == 'desktop index'
            asset = c.get('/desktop/asset.js')
            assert asset.status_code == 200 and asset.headers['cache-control'] == 'no-store'
            assert 'default-src' in asset.headers['content-security-policy']
            assert c.get('/desktop/escape.js').status_code == 404
            assert c.get('/desktop/%2e%2e/outside.pv').status_code == 404
            recs = c.get('/api/recordings').json()['recordings']
            assert {r['key'] for r in recs} == {f'local/{x}' for x in (txt, wav, enc)}, recs
            assert all(r['source'] == 'local' and r['ingested_at'] for r in recs)
            for rid in (missing, escaped, linked, incomplete):
                for endpoint in ('rec', 'audio', 'doc'):
                    assert c.get(f'/api/{endpoint}/local/{rid}', params={'name': 'transcript.md'}).status_code == 404
            assert c.get(f'/api/rec/local/{txt}').json() == {'key': f'local/{txt}', 'docs': ['transcript.md'], 'has_audio': False}
            html = c.get(f'/api/doc/local/{txt}', params={'name':'transcript.md'}).json()['html']
            assert '<script>' not in html and '&lt;script&gt;' in html and '<img' not in html
            assert c.get(f'/api/doc/local/{txt}', params={'name':'../vault.db'}).status_code == 404
            results = c.get('/api/search', params={'q':'presupuesto'}).json()['results']
            assert {r['key'] for r in results} == {f'local/{x}' for x in (txt, wav, enc)}, results
            assert c.get(f'/api/audio/local/{enc}').status_code == 401
            app.state.vault.key = b'fakekey'
            with patch('plaudvault.gui.decrypt_bytes', return_value=b'fLaCdecoded') as decrypt:
                audio = c.get(f'/api/audio/local/{enc}', headers={'Range':'bytes=0-3'})
                assert audio.status_code == 206 and audio.content == b'fLaC'
                assert audio.headers['content-type'] == 'audio/flac'
                decrypt.assert_called_once()
            assert c.get(f'/api/audio/local/{wav}').headers['content-type'] == 'audio/wav'
            c.post('/api/lock')
            assert c.get(f'/api/audio/local/{wav}').status_code == 401
            cfg.crypto_enabled = False
            state = c.get('/api/recordings').json()
            assert state['locked'] is False and state['crypto_enabled'] is False
            assert not cfg.salt_path.exists() and not cfg.keycheck_path.exists()
            plain = c.get(f'/api/audio/local/{wav}')
            assert plain.status_code == 200 and plain.content == b'RIFFfakewave'
            assert c.get(f'/api/audio/local/{enc}').status_code == 401
            assert c.get('/api/rec/local/nope').status_code == 404
            assert c.get('/api/rec/local/1/extra').status_code == 404
        print('✅ test_local')

if __name__ == '__main__':
    test_local()
