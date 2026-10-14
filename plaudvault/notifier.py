"""notifier.py — Aviso por Telegram cuando se procesa una grabación.

Decisión consciente para el MVP: hablamos con la Bot API por HTTP directo
(requests) en vez de usar python-telegram-bot. Esa librería brilla cuando
el bot ESCUCHA comandos (polling, handlers, estado); para solo ENVIAR un
mensaje es matar moscas a cañonazos. Cuando en una fase posterior el bot
sea bidireccional (mandarle audios, pedirle búsquedas), migramos.

Credenciales por variables de entorno, nunca en config.yaml — el config
puede acabar en un pastebin por accidente; el entorno, difícilmente.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("plaudvault.notifier")


class TelegramNotifier:
    def __init__(self, token_env: str, chat_id_env: str, enabled: bool):
        self.enabled = enabled
        self.token = os.environ.get(token_env, "")
        self.chat_id = os.environ.get(chat_id_env, "")
        if self.enabled and not (self.token and self.chat_id):
            logger.warning(
                "Telegram activado en config pero faltan %s/%s en el entorno. "
                "Notificaciones desactivadas.", token_env, chat_id_env,
            )
            self.enabled = False

    def notify(self, text: str) -> None:
        """Best-effort: si Telegram falla, se loguea y la vida sigue.
        Una notificación caída jamás debe tumbar el pipeline."""
        if not self.enabled:
            return
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("No se pudo notificar por Telegram: %s", exc)
