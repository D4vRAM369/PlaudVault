"""Comprueba un .deb extraído con datos ficticios, sin instalarlo en el host."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time


def run_command(arguments: list[str], environment: dict[str, str]) -> str:
    result = subprocess.run(arguments, env=environment, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"Falló {arguments[1:]}: {result.stdout}\n{result.stderr}")
    return result.stdout


def profiles() -> set[Path]:
    roots = {Path('/dev/shm')}
    if os.environ.get('XDG_RUNTIME_DIR'):
        roots.add(Path(os.environ['XDG_RUNTIME_DIR']))
    return {path for root in roots for path in root.glob('plaudvault-desktop-*')}


def check_gui(executable: Path, config: Path, environment: dict[str, str]) -> None:
    before = profiles()
    process = subprocess.Popen([str(executable), 'desktop', '-c', str(config)],
                               env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    created = set()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if selector.select(timeout=0.2):
                chunk = os.read(process.stdout.fileno(), 65536)
                output.extend(chunk)
                if b'renderer en sandbox' in output:
                    break
                if not chunk:
                    raise RuntimeError('El escritorio terminó antes de verificar el sandbox: ' + output.decode(errors='replace'))
        else:
            raise RuntimeError('El escritorio no verificó el sandbox en 30 segundos: ' + output.decode(errors='replace'))
        created = profiles() - before
        if not created:
            raise RuntimeError('No apareció el perfil efímero en RAM')
        # La línea aparece justo antes de server.run; dejar que instale SIGINT.
        time.sleep(1)
        process.send_signal(signal.SIGINT)
        remainder, _ = process.communicate(timeout=15)
        output.extend(remainder)
        if process.returncode != 0:
            raise RuntimeError(f'Cierre de escritorio con código {process.returncode}: ' + output.decode(errors='replace'))
        if any(path.exists() for path in created):
            raise RuntimeError('El perfil en RAM no se eliminó al cerrar')
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
    print('✅ Escritorio: sandbox verificado, cierre correcto y perfil RAM eliminado')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deb', required=True, type=Path)
    parser.add_argument('--gui', action='store_true', help='abre Tauri en la sesión gráfica actual')
    arguments = parser.parse_args()
    package = arguments.deb.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='plaudvault-package-') as temporary:
        root = Path(temporary)
        extracted = root / 'extracted'
        subprocess.run(['dpkg-deb', '--extract', str(package), str(extracted)], check=True)
        executable = extracted / 'usr/bin/plaudvault'
        if not executable.is_symlink() or os.readlink(executable) != '../lib/plaudvault/plaudvault':
            raise RuntimeError('El lanzador debe ser un enlace relativo al ejecutable incluido')
        bundle = extracted / 'usr/lib/plaudvault'
        internal = bundle / '_internal'
        for resource in (internal / 'config.example.yaml', internal / 'desktop/dist/index.html',
                         internal / 'desktop/src-tauri/target/release/plaudvault-desktop'):
            if not resource.is_file():
                raise RuntimeError(f'Falta recurso empaquetado: {resource.relative_to(extracted)}')
        if not os.access(executable, os.X_OK) or not os.access(internal / 'desktop/src-tauri/target/release/plaudvault-desktop', os.X_OK):
            raise RuntimeError('Los ejecutables incluidos no tienen permiso de ejecución')
        desktop_files = list((extracted / 'usr/share/applications').glob('*plaudvault*.desktop'))
        if len(desktop_files) != 1:
            raise RuntimeError('Falta un único acceso .desktop de PlaudVault')
        metadata = desktop_files[0].read_text()
        for value in ('[Desktop Entry]', 'Type=Application', 'Exec=/usr/bin/plaudvault desktop', 'Icon=plaudvault'):
            if value not in metadata:
                raise RuntimeError(f'Falta metadato: {value}')
        if not list((extracted / 'usr/share/icons').rglob('plaudvault.png')):
            raise RuntimeError('Falta icono instalable')
        allowed = ('PATH', 'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS',
                   'XDG_RUNTIME_DIR', 'LANG', 'LC_ALL', 'LC_CTYPE')
        environment = {key: os.environ[key] for key in allowed if key in os.environ}
        for key, folder in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data')):
            location = root / folder
            location.mkdir()
            environment[key] = str(location)
        run_command([str(executable), '--help'], environment)
        run_command([str(executable), 'desktop', '--help'], environment)
        vault = root / 'vault'
        config = root / 'fixture.yaml'
        config.write_text('\n'.join([
            'vault:', f'  base_path: {json.dumps(str(vault))}',
            f'  db_path: {json.dumps(str(vault / "vault.db"))}',
            f'  inbox_path: {json.dumps(str(vault / "inbox"))}',
            f'  archive_path: {json.dumps(str(vault / "archive"))}',
            'crypto:', '  enabled: false', 'telegram:', '  enabled: false',
            'logging:', f'  file: {json.dumps(str(root / "fixture.log"))}',
        ]))
        def cli(*values: str) -> str:
            return run_command([str(executable), '-c', str(config), *values], environment)
        cli('init')
        text = root / 'recording.txt'
        text.write_text('Reunión ficticia: evaluación de búsqueda y sincronización.', encoding='utf-8')
        cli('ingest', str(text))
        found = cli('search', 'evaluacion')
        if 'evaluación' not in found:
            raise RuntimeError('La búsqueda sin tildes no recuperó el texto ficticio')
        if 'recording' not in cli('list'):
            raise RuntimeError('El listado no muestra la ingesta ficticia')
        print('✅ Paquete extraído: recursos, ayuda, init, ingesta, búsqueda sin tildes y listado')
        if arguments.gui:
            check_gui(executable, config, environment)


if __name__ == '__main__':
    main()
