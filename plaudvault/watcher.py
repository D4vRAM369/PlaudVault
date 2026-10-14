"""watcher.py — Vigila el inbox y dispara el pipeline con cada fichero nuevo.

watchdog se suscribe a los eventos del sistema de ficheros (inotify en
Linux): en vez de preguntar cada X segundos "¿hay algo nuevo?" (polling),
el kernel nos avisa en el momento exacto. Como un timbre frente a salir
a mirar a la puerta cada minuto.

Sutileza importante — el problema del fichero a medias: cuando Syncthing
o una copia manual escriben un audio grande, el evento 'created' llega
cuando el fichero EMPIEZA a existir, no cuando termina de copiarse. Si lo
procesamos al instante, transcribimos medio fichero. Solución del MVP:
esperar a que el tamaño se estabilice antes de procesar.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .pipeline import SUPPORTED, Pipeline

logger = logging.getLogger("plaudvault.watcher")

STABILITY_CHECKS = 3      # nº de lecturas consecutivas con el mismo tamaño
STABILITY_INTERVAL = 1.0  # segundos entre lecturas


def wait_until_stable(path: Path) -> bool:
    """Devuelve True cuando el fichero deja de crecer (copia terminada)."""
    last_size = -1
    stable = 0
    while stable < STABILITY_CHECKS:
        if not path.exists():
            return False  # lo borraron a mitad de copia
        size = path.stat().st_size
        if size == last_size and size > 0:
            stable += 1
        else:
            stable = 0
            last_size = size
        time.sleep(STABILITY_INTERVAL)
    return True


class InboxHandler(FileSystemEventHandler):
    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in SUPPORTED:
            return
        if not wait_until_stable(path):
            return
        try:
            self.pipeline.process_file(path)
        except Exception:
            # Un fichero problemático no debe tumbar el watcher entero.
            logger.exception("Error procesando %s", path.name)


def run_watcher(pipeline: Pipeline, inbox: Path) -> None:
    """Bucle principal: procesa lo pendiente y se queda vigilando."""
    inbox.mkdir(parents=True, exist_ok=True)

    pending = pipeline.process_inbox()
    if pending:
        logger.info("Procesados %d ficheros pendientes del inbox.", pending)

    observer = Observer()
    observer.schedule(InboxHandler(pipeline), str(inbox), recursive=False)
    observer.start()
    logger.info("👁  Vigilando %s — Ctrl+C para salir.", inbox)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Deteniendo watcher…")
        observer.stop()
    observer.join()
