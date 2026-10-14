"""config.py — Carga y validación de la configuración del vault.

Por qué existe este módulo: centralizar la lectura de config.yaml en UN sitio.
Si mañana cambias una ruta o añades una opción, solo tocas aquí y el resto
del sistema la recibe ya expandida y validada. Sin esto, cada módulo haría
su propio os.path.expanduser() y tendrías bugs de rutas inconsistentes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass
class VaultPaths:
    base: Path
    db: Path
    inbox: Path
    archive: Path


@dataclass
class Config:
    """Configuración completa del sistema, ya expandida y lista para usar."""

    paths: VaultPaths
    # Transcripción
    whisper_model: str = "small"
    language: str = "es"
    device: str = "cpu"
    compute_type: str = "int8"
    # Crypto
    crypto_enabled: bool = True
    # Telegram
    telegram_enabled: bool = False
    telegram_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_chat_id_env: str = "TELEGRAM_CHAT_ID"
    # Logging
    log_level: str = "INFO"
    log_file: Path = field(default_factory=lambda: Path("plaudvault.log"))

    @property
    def salt_path(self) -> Path:
        """El salt de Argon2id vive junto a la base de datos."""
        return self.paths.base / "vault.salt"

    @property
    def keycheck_path(self) -> Path:
        """Fichero pequeño cifrado que sirve para verificar la passphrase."""
        return self.paths.base / "vault.keycheck"


def _expand(raw: str) -> Path:
    """Convierte '~/PlaudVault' en '/home/usuario/PlaudVault'."""
    return Path(os.path.expanduser(raw)).resolve()


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """Lee config.yaml y devuelve un objeto Config tipado.

    Lanza FileNotFoundError con mensaje claro si no existe — mejor fallar
    pronto y con explicación que arrastrar un None por todo el sistema.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Copia config.example.yaml a config.yaml y ajústalo."
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    vault = raw.get("vault", {})
    paths = VaultPaths(
        base=_expand(vault.get("base_path", "~/PlaudVault")),
        db=_expand(vault.get("db_path", "~/PlaudVault/vault.db")),
        inbox=_expand(vault.get("inbox_path", "~/PlaudVault/inbox")),
        archive=_expand(vault.get("archive_path", "~/PlaudVault/archive")),
    )

    trans = raw.get("transcription", {})
    crypto = raw.get("crypto", {})
    tg = raw.get("telegram", {})
    log = raw.get("logging", {})

    return Config(
        paths=paths,
        whisper_model=trans.get("model", "small"),
        language=trans.get("language", "es"),
        device=trans.get("device", "cpu"),
        compute_type=trans.get("compute_type", "int8"),
        crypto_enabled=crypto.get("enabled", True),
        telegram_enabled=tg.get("enabled", False),
        telegram_token_env=tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"),
        telegram_chat_id_env=tg.get("chat_id_env", "TELEGRAM_CHAT_ID"),
        log_level=log.get("level", "INFO"),
        log_file=_expand(log.get("file", "~/PlaudVault/plaudvault.log")),
    )
