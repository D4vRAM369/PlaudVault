"""Construye .deb y tar.gz de Ubuntu 24.04 amd64 sin instalar en el sistema."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def debian_version(version: str) -> str:
    match = re.fullmatch(r'(\d+\.\d+\.\d+)-rc\.(\d+)', version)
    if not match:
        raise ValueError('La primera distribución admite versiones X.Y.Z-rc.N')
    return f'{match[1]}~rc{match[2]}'


def stage_debian(bundle: Path, stage: Path, version: str, project: Path) -> None:
    deb_version = debian_version(version)
    destination = stage / 'usr/lib/plaudvault'
    shutil.copytree(bundle, destination, symlinks=True)
    binaries = stage / 'usr/bin'
    binaries.mkdir(parents=True)
    (binaries / 'plaudvault').symlink_to('../lib/plaudvault/plaudvault')
    applications = stage / 'usr/share/applications'
    applications.mkdir(parents=True)
    (applications / 'plaudvault.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=PlaudVault\n'
        'Comment=Bóveda local de grabaciones de voz\n'
        'Exec=/usr/bin/plaudvault desktop\nIcon=plaudvault\n'
        'Terminal=false\nCategories=AudioVideo;Audio;\nStartupNotify=true\n')
    icon = stage / 'usr/share/icons/hicolor/512x512/apps'
    icon.mkdir(parents=True)
    shutil.copy2(project / 'assets/icon.png', icon / 'plaudvault.png')
    documentation = stage / 'usr/share/doc/plaudvault'
    documentation.mkdir(parents=True)
    shutil.copy2(project / 'LICENSE', documentation / 'copyright')
    shutil.copy2(project / 'config.example.yaml', documentation / 'config.example.yaml')
    control = stage / 'DEBIAN'
    control.mkdir()
    size = sum(p.stat().st_size for p in destination.rglob('*') if p.is_file() and not p.is_symlink()) // 1024
    (control / 'control').write_text(
        f'Package: plaudvault\nVersion: {deb_version}\nArchitecture: amd64\n'
        'Maintainer: D4vRAM369\nSection: sound\nPriority: optional\n'
        f'Installed-Size: {size}\n'
        'Depends: libc6 (>= 2.39), libwebkit2gtk-4.1-0 (>= 2.44), libgtk-3-0t64, '
        'libstdc++6, bubblewrap, gstreamer1.0-plugins-good, gstreamer1.0-libav\n'
        'Homepage: https://github.com/D4vRAM369/PlaudVault\n'
        'Description: Boveda local de grabaciones de voz (experimental)\n'
        ' Incluye CLI, Python y escritorio Tauri con audio cifrado.\n'
        ' Los textos quedan en claro. Requiere sesion grafica Linux y tmpfs.\n')


def add_licenses(bundle: Path) -> None:
    target = bundle / 'third-party-licenses'
    target.mkdir()
    notices = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata.get('Name', '').lower()):
        name = distribution.metadata.get('Name', 'unknown')
        notices.append(f'{name} {distribution.version}')
        for relative in distribution.files or []:
            basename = Path(str(relative)).name.lower()
            if 'license' not in basename and 'copying' not in basename and 'notice' not in basename:
                continue
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                folder = target / re.sub(r'[^a-zA-Z0-9_.-]', '_', name)
                folder.mkdir(exist_ok=True)
                shutil.copy2(source, folder / source.name)
    for name in ('python3.12', 'python3.12-minimal'):
        source = Path('/usr/share/doc') / name / 'copyright'
        if source.is_file(): shutil.copy2(source, target / f'{name}-copyright')
    (target / 'DISTRIBUTIONS.txt').write_text('\n'.join(notices) + '\n')


def freeze(native: Path, work: Path) -> Path:
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
               '--name', 'plaudvault', '--distpath', str(work / 'frozen'),
               '--workpath', str(work / 'analysis'), '--specpath', str(work / 'spec'),
               '--paths', str(ROOT), '--add-data', f'{ROOT / "config.example.yaml"}:.',
               '--add-data', f'{ROOT / "plaudvault/gui_static"}:plaudvault/gui_static',
               '--add-data', f'{ROOT / "desktop/dist"}:desktop/dist',
               '--add-binary', f'{native}:desktop/src-tauri/target/release']
    for package in ('faster_whisper', '_cffi_backend', 'watchdog.observers.inotify',
                    'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
                    'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.websockets.auto',
                    'uvicorn.lifespan.on'):
        command += ['--hidden-import', package]
    for package in ('faster_whisper',): command += ['--collect-data', package]
    for package in ('ctranslate2', 'av', 'onnxruntime'): command += ['--collect-binaries', package]
    for distribution in ('faster-whisper', 'fastapi', 'uvicorn', 'cryptography', 'argon2-cffi', 'PyYAML', 'watchdog', 'requests', 'markdown-it-py'):
        command += ['--recursive-copy-metadata', distribution]
    command.append(str(ROOT / 'packaging/entrypoint.py'))
    subprocess.run(command, check=True, cwd=ROOT)
    return work / 'frozen/plaudvault'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist/release')
    args = parser.parse_args()
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        parser.error('Este paquete se construye solo en Linux x86_64')
    deb_version = debian_version(args.version)
    native = args.native.resolve(strict=True)
    if not (ROOT / 'desktop/dist/index.html').is_file():
        parser.error('Compila antes el frontend de desktop/')
    native_version = json.loads((ROOT / 'desktop/src-tauri/tauri.conf.json').read_text())['version']
    python_version = re.search(r'(?m)^version = "([^"]+)"', (ROOT / 'pyproject.toml').read_text())[1]
    if native_version != args.version or python_version != args.version.replace('-rc.', 'rc'):
        parser.error('Las versiones de Python, Tauri y release deben coincidir')
    work = ROOT / 'build/linux-release'
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)
    bundle = freeze(native, work)
    for name in ('LICENSE', 'config.example.yaml'):
        shutil.copy2(ROOT / name, bundle / name)
    (bundle / 'README.md').write_text((ROOT / 'packaging/README.md').read_text().replace('../assets/', 'assets/'))
    shutil.copy2(ROOT / 'packaging/RELEASE_NOTES.md', bundle / 'RELEASE_NOTES.md')
    (bundle / 'assets').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'assets/tauri-security.md', bundle / 'assets/tauri-security.md')
    add_licenses(bundle)
    # Un bundle distribuible no puede enlazar bibliotecas al equipo de construcción.
    for path in bundle.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(bundle.resolve()):
            raise RuntimeError(f'Enlace fuera del bundle: {path.relative_to(bundle)}')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    portable_name = f'PlaudVault-{args.version}-linux-x86_64'
    portable = output / f'{portable_name}.tar.gz'
    with tarfile.open(portable, 'w:gz') as archive:
        archive.add(bundle, arcname=portable_name)
    stage = work / 'debian'
    stage_debian(bundle, stage, args.version, ROOT)
    deb = output / f'plaudvault_{deb_version}_amd64.deb'
    subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(deb)], check=True)
    (output / 'SHA256SUMS').write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in (deb, portable)))
    print(f'Paquetes preparados en {output}')


if __name__ == '__main__': main()
