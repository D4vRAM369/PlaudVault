"""Estructura instalable y lanzador seguro, sin construir ni instalar en el host."""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('pv_linux_build', ROOT / 'packaging/build_linux.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_installer_layout():
    assert module.debian_version('0.3.0-rc.1') == '0.3.0~rc1'
    for invalid in ('0.3.0', '../bad', '0.3.0-rc.1\n'):
        try: module.debian_version(invalid)
        except ValueError: pass
        else: raise AssertionError(invalid)
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp);bundle=base/'bundle';bundle.mkdir()
        (bundle/'plaudvault').write_text('fake executable')
        (bundle/'plaudvault').chmod(0o755)
        stage=base/'stage'
        module.stage_debian(bundle,stage,'0.3.0-rc.1',ROOT)
        link=stage/'usr/bin/plaudvault'
        assert link.is_symlink() and link.resolve()==stage/'usr/lib/plaudvault/plaudvault'
        desktop=(stage/'usr/share/applications/plaudvault.desktop').read_text()
        assert 'Exec=/usr/bin/plaudvault desktop' in desktop
        assert 'plaudvault-desktop' not in desktop
        control=(stage/'DEBIAN/control').read_text()
        assert 'Version: 0.3.0~rc1' in control and 'Architecture: amd64' in control
        assert 'bubblewrap' in control and 'libwebkit2gtk-4.1-0' in control
        assert (stage/'usr/share/doc/plaudvault/config.example.yaml').is_file()
    print('✅ test_installer_layout')


if __name__ == '__main__': test_installer_layout()
