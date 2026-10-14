"""crypto.py — Cifrado de los audios originales.

Modelo de amenaza (fijado en el spec): si te roban el disco, nadie puede
escuchar tus audios. NO protege contra malware corriendo en tu sesión.

Dos piezas, dos trabajos:

1. Argon2id (derivación de clave): convierte tu passphrase humana en una
   clave de 32 bytes. Analogía: tu passphrase es masa de pan; Argon2id es
   un amasado deliberadamente lento y costoso en RAM. A ti te cuesta ~1
   segundo una vez; a un atacante probando millones de passphrases le
   cuesta una eternidad. Por eso se usa Argon2id y no un hash rápido.

2. AES-256-GCM (cifrado autenticado): cifra el fichero Y detecta
   manipulación. Si alguien cambia un solo byte del cifrado, el descifrado
   falla en vez de devolverte basura silenciosamente.

La clave maestra se pide en runtime y NUNCA toca el disco. Lo único que
se guarda es el salt (público por diseño: su trabajo es que dos vaults
con la misma passphrase tengan claves distintas).

Formato del fichero .pv:  [magic 4B][nonce 12B][ciphertext+tag]
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"PLV1"          # identifica el formato y permite versionarlo en el futuro
SALT_BYTES = 16
NONCE_BYTES = 12          # tamaño estándar de nonce para GCM
KEY_BYTES = 32            # 32 bytes = AES-256

# Parámetros Argon2id (perfil interactivo razonable en 2026 para escritorio):
ARGON_TIME_COST = 3       # iteraciones
ARGON_MEMORY_KIB = 65536  # 64 MiB de RAM — esto es lo que duele al atacante
ARGON_PARALLELISM = 4

KEYCHECK_PLAINTEXT = b"plaudvault-keycheck-v1"


class WrongPassphrase(Exception):
    """La passphrase no corresponde a este vault."""


def generate_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """passphrase humana + salt → clave AES de 32 bytes (determinista)."""
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=ARGON_TIME_COST,
        memory_cost=ARGON_MEMORY_KIB,
        parallelism=ARGON_PARALLELISM,
        hash_len=KEY_BYTES,
        type=Type.ID,  # Argon2id: híbrido resistente a GPU y side-channels
    )


def encrypt_bytes(key: bytes, plaintext: bytes) -> bytes:
    """Cifra un blob. El nonce es aleatorio y va pegado delante del cifrado.

    Regla de oro de GCM: NUNCA reutilizar un par (clave, nonce). Con nonce
    aleatorio de 12 bytes y un volumen de ficheros personal, la probabilidad
    de colisión es despreciable.
    """
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=MAGIC)
    return MAGIC + nonce + ciphertext


def decrypt_bytes(key: bytes, blob: bytes) -> bytes:
    if blob[:4] != MAGIC:
        raise ValueError("Esto no es un fichero PlaudVault (.pv) válido.")
    nonce = blob[4 : 4 + NONCE_BYTES]
    ciphertext = blob[4 + NONCE_BYTES :]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, associated_data=MAGIC)
    except InvalidTag as exc:
        # InvalidTag = passphrase incorrecta O fichero manipulado.
        raise WrongPassphrase(
            "Descifrado fallido: passphrase incorrecta o fichero corrupto."
        ) from exc


def encrypt_file(key: bytes, src: Path, dst: Path) -> None:
    """Cifra src → dst de forma atómica: escribe a .tmp y renombra al final.
    Si el proceso muere a mitad, nunca queda un .pv corrupto a medias."""
    data = src.read_bytes()
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_bytes(encrypt_bytes(key, data))
    os.replace(tmp, dst)


def decrypt_file(key: bytes, src: Path, dst: Path) -> None:
    dst.write_bytes(decrypt_bytes(key, src.read_bytes()))


# ---------- gestión del vault ----------

def init_vault_crypto(salt_path: Path, keycheck_path: Path, passphrase: str) -> None:
    """Se ejecuta una vez en `init`: genera el salt y un fichero 'keycheck'
    (un texto conocido, cifrado). Sirve para verificar la passphrase al
    arrancar SIN guardar nunca la clave ni un hash de ella."""
    salt = generate_salt()
    salt_path.write_bytes(salt)
    key = derive_key(passphrase, salt)
    keycheck_path.write_bytes(encrypt_bytes(key, KEYCHECK_PLAINTEXT))


def unlock(salt_path: Path, keycheck_path: Path, passphrase: str) -> bytes:
    """Deriva la clave y la verifica contra el keycheck. Devuelve la clave
    para usarla en memoria durante la sesión. Lanza WrongPassphrase si no
    corresponde."""
    salt = salt_path.read_bytes()
    key = derive_key(passphrase, salt)
    if decrypt_bytes(key, keycheck_path.read_bytes()) != KEYCHECK_PLAINTEXT:
        raise WrongPassphrase("La passphrase no corresponde a este vault.")
    return key
