"""Lanzador Linux de Tauri: sesión efímera en RAM y API compartida.

El binario abre primero una página vacía. Solo recibe el enlace autenticado
por stdin cuando el padre ha comprobado el sandbox del renderer en /proc.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .config import load_config


def path_in_ram(path: Path) -> bool:
    """Resolver el montaje más específico; un directorio de /run puede ser disco."""
    try:
        target = path.resolve()
        matches = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            before, after = line.split(" - ", 1)
            raw = before.split()[4]
            mount = Path(raw.replace("\\040", " ").replace("\\134", "\\"))
            if target.is_relative_to(mount):
                matches.append((len(mount.parts), after.split()[0]))
        return bool(matches) and max(matches, key=lambda item: item[0])[1] in {"tmpfs", "ramfs"}
    except (OSError, ValueError, IndexError):
        return False


def runtime_environment(root: Path) -> dict[str, str]:
    # Lista permitida: el webview no necesita tokens, claves ni flags del usuario.
    keys = ("PATH", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
            "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "LC_CTYPE")
    env = {key: os.environ[key] for key in keys if key in os.environ}
    for key, name in (("HOME", "home"), ("XDG_CACHE_HOME", "cache"),
                      ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                      ("TMPDIR", "tmp"), ("MESA_SHADER_CACHE_DIR", "mesa")):
        directory = root / name
        directory.mkdir(mode=0o700, exist_ok=True)
        env[key] = str(directory)
    env["GST_REGISTRY"] = str(root / "cache" / "gstreamer-registry.bin")
    env["WEBKIT_FORCE_SANDBOX"] = "1"
    return env


def webkit_process_problem(status: str) -> str | None:
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    if fields.get("NoNewPrivs", "").strip() != "1":
        return "renderer sin NoNewPrivs"
    if fields.get("Seccomp", "").strip() != "2":
        return "renderer sin filtro seccomp"
    return None


def webkit_sandbox_problem(pid: int, timeout: float = 20) -> str | None:
    deadline = time.monotonic() + timeout
    reason = "no apareció ningún renderer WebKit que comprobar"
    while time.monotonic() < deadline:
        processes = {}
        for path in Path("/proc").glob("[0-9]*/status"):
            try:
                status = path.read_text()
                parent = int(next(line.split()[1] for line in status.splitlines()
                                  if line.startswith("PPid:")))
                processes[int(path.parent.name)] = (parent, status, path.parent)
            except (OSError, ValueError, StopIteration):
                continue
        descendants = {pid}
        while True:
            children = {child for child, (parent, _, _) in processes.items() if parent in descendants}
            if children.issubset(descendants):
                break
            descendants.update(children)
        problems = []
        seen = False
        for child in descendants:
            if child not in processes:
                continue
            _, status, proc = processes[child]
            try:
                command = proc.joinpath("cmdline").read_bytes()
            except OSError:
                continue
            # bubblewrap incluye la ruta del renderer entre sus argumentos;
            # comprobar su proceso lanzador daría un falso fallo de seccomp.
            executable = command.split(b"\0", 1)[0].rsplit(b"/", 1)[-1]
            if executable == b"WebKitWebProcess":
                seen = True
                problem = webkit_process_problem(status)
                if problem:
                    problems.append(problem)
        if seen and not problems:
            return None
        if problems:
            reason = problems[0]
        time.sleep(0.2)
    return reason


def main() -> None:
    parser = argparse.ArgumentParser(description="PlaudVault escritorio Tauri (Linux, solo lectura)")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--binary", type=Path, help="binario plaudvault-desktop compilado")
    parser.add_argument("--frontend", type=Path, help="directorio dist de Svelte compilado")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.exit(1, "El escritorio seguro requiere Linux.\n")
    from .gui import create_app, kill_group, ram_dir
    import uvicorn

    project = Path(__file__).resolve().parent.parent
    frontend = (args.frontend or project / "desktop" / "dist").resolve()
    targets = project / "desktop" / "src-tauri" / "target"
    binary = args.binary or next((targets / profile / "plaudvault-desktop"
                                 for profile in ("release", "debug")
                                 if (targets / profile / "plaudvault-desktop").is_file()),
                                targets / "debug" / "plaudvault-desktop")
    binary = binary.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.exit(1, "Compila primero el binario de desktop/src-tauri (ver desktop/README.md).\n")
    if not (frontend / "index.html").is_file():
        parser.exit(1, "Compila primero el frontend en desktop/ (npm run build).\n")
    ram = ram_dir()
    if ram is None:
        parser.exit(1, "No hay tmpfs verificado: no se abre el escritorio.\n")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not path_in_ram(Path(runtime)):
        parser.exit(1, "XDG_RUNTIME_DIR debe existir en tmpfs: no se abre el escritorio.\n")
    cfg = load_config(args.config)
    profile = Path(tempfile.mkdtemp(prefix="plaudvault-desktop-", dir=ram))
    process = None
    server = None
    app = None
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        token = secrets.token_urlsafe(32)
        app = create_app(cfg, token, {f"127.0.0.1:{port}"}, frontend_dir=frontend)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="warning", access_log=False))
        process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=runtime_environment(profile), start_new_session=True)
        problem = webkit_sandbox_problem(process.pid)
        if process.poll() is not None or problem:
            parser.exit(1, f"Escritorio cerrado: no se pudo verificar el sandbox ({problem or 'proceso terminado'}).\n")

        def connect_when_ready():
            deadline = time.monotonic() + 15
            while not server.started and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if server.started and process.poll() is None:
                try:
                    process.stdin.write(f"http://127.0.0.1:{port}/login?t={token}\n".encode())
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    server.should_exit = True
            else:
                server.should_exit = True

        def wait_for_close():
            process.wait()
            server.should_exit = True

        threading.Thread(target=app.state.vault.auto_lock_loop, daemon=True).start()
        threading.Thread(target=wait_for_close, daemon=True).start()
        threading.Thread(target=connect_when_ready, daemon=True).start()
        print("PlaudVault: escritorio local con perfil en RAM y renderer en sandbox. Cierra la ventana para salir.", flush=True)
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            pass
    finally:
        if app is not None:
            app.state.vault.lock_now()
        if server is not None:
            server.should_exit = True
        if process is not None:
            kill_group(process)
            process.wait(timeout=5)
        listener.close()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
