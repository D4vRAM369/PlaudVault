"""Punto de entrada del paquete Linux; no crea configuración al arrancar."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from plaudvault.__main__ import main as cli_main
from plaudvault.desktop import main as desktop_main


def default_config() -> Path:
    config_home = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    user_config = config_home / 'plaudvault' / 'config.yaml'
    if user_config.is_file():
        return user_config
    return Path(__file__).resolve().parent / 'config.example.yaml'


def main() -> None:
    arguments = sys.argv[1:]
    # Saltar solo opciones globales conocidas: una consulta «desktop» no es
    # el subcomando desktop, y una ruta de configuración puede llamarse así.
    command_index = 0
    while command_index < len(arguments):
        argument = arguments[command_index]
        if argument in ('-c', '--config'):
            command_index += 2
        elif argument.startswith('--config=') or (argument.startswith('-c') and len(argument) > 2):
            command_index += 1
        else:
            break
    desktop = command_index < len(arguments) and arguments[command_index] == 'desktop'
    if desktop:
        arguments = arguments[:command_index] + arguments[command_index + 1:]
    if not any(argument in ('-c', '--config') or argument.startswith('--config=')
               or (argument.startswith('-c') and len(argument) > 2)
               for argument in arguments):
        arguments = ['-c', str(default_config()), *arguments]
    sys.argv = [sys.argv[0], *arguments]
    (desktop_main if desktop else cli_main)()


if __name__ == '__main__':
    main()
