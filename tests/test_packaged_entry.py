"""El ejecutable distribuido elige configuración sin modificar el disco."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run():
    entry_path = Path(__file__).resolve().parents[1] / 'packaging' / 'entrypoint.py'
    spec = importlib.util.spec_from_file_location('packaged_entry', entry_path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        home = root / 'home'
        home.mkdir()
        xdg = root / 'xdg'
        bundle = root / '_internal'
        bundle.mkdir()
        template = bundle / 'config.example.yaml'
        template.write_text('example only')
        with patch.dict(os.environ, {'HOME': str(home), 'XDG_CONFIG_HOME': str(xdg)}), patch.object(entry, '__file__', str(bundle / 'entrypoint.py')):
            assert entry.default_config() == template
            user_config = xdg / 'plaudvault' / 'config.yaml'
            user_config.parent.mkdir(parents=True)
            user_config.write_text('user config')
            assert entry.default_config() == user_config
            before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
            for arguments, expected, desktop in [
                (['search', 'voz'], ['-c', str(user_config), 'search', 'voz'], False),
                (['desktop'], ['-c', str(user_config)], True),
                (['desktop', '-c', 'custom.yaml'], ['-c', 'custom.yaml'], True),
                (['--config', 'custom.yaml', 'list'], ['--config', 'custom.yaml', 'list'], False),
                (['--config=custom.yaml', 'list'], ['--config=custom.yaml', 'list'], False),
                (['desktop', '--config=custom.yaml'], ['--config=custom.yaml'], True),
                (['-c', 'custom.yaml', 'desktop'], ['-c', 'custom.yaml'], True),
                (['-ccustom.yaml', 'desktop'], ['-ccustom.yaml'], True),
                (['search', 'desktop'], ['-c', str(user_config), 'search', 'desktop'], False),
            ]:
                observed = []
                with patch.object(sys, 'argv', ['plaudvault', *arguments]), patch.object(entry, 'cli_main', lambda: observed.append(('cli', sys.argv[1:]))), patch.object(entry, 'desktop_main', lambda: observed.append(('desktop', sys.argv[1:]))):
                    entry.main()
                assert observed == [('desktop' if desktop else 'cli', expected)], observed
            assert sorted(str(path.relative_to(root)) for path in root.rglob('*')) == before
        with patch.dict(os.environ, {'HOME': str(home)}, clear=True), patch.object(entry, '__file__', str(bundle / 'entrypoint.py')):
            assert entry.default_config() == template
            config = home / '.config' / 'plaudvault' / 'config.yaml'
            config.parent.mkdir(parents=True)
            config.write_text('home config')
            assert entry.default_config() == config
    print('✅ packaged entry: routing, configuration overrides and no writes')


if __name__ == '__main__':
    run()
