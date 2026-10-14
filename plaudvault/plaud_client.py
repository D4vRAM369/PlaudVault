"""plaud_client.py — Cliente mínimo de la API web (no oficial) de Plaud.

Por qué existe: Plaud no tiene API pública. La web (web.plaud.ai) habla con
api.plaud.ai usando un JWT; replicamos esas mismas llamadas. Todo lo que hay
aquí se verificó con una sonda contra la API real (ver docs/), no se supuso.

Si Plaud cambia su API, este es el ÚNICO fichero que habría que tocar.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re

import requests

logger = logging.getLogger("plaudvault.plaud")

DEFAULT_API = "https://api.plaud.ai"
PAGE_SIZE = 50


class PlaudError(Exception):
    """Error de la API de Plaud (token inválido, respuesta inesperada…)."""


JWT_RE = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")


def extract_jwt(pasted: str) -> str:
    """Saca el JWT de lo que se haya pegado: valor solo, con "bearer ", con
    comillas o con la línea entera "authorization: bearer …" de DevTools.
    Si no hay JWT, el error describe la FORMA del texto, nunca su contenido."""
    m = JWT_RE.search(pasted)
    if m:
        return m.group(0)
    raise PlaudError(
        f"PLAUD_TOKEN no contiene un JWT (len={len(pasted)}, "
        f"empieza_eyJ={pasted.strip().startswith('eyJ')}, "
        f"puntos={pasted.count('.')}). Cópialo de DevTools → Network → "
        "Request Headers → authorization → clic derecho → Copy value."
    )


class PlaudClient:
    def __init__(self, token: str, api: str = DEFAULT_API):
        raw = extract_jwt(token)
        self.api = api.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"bearer {raw}",
            # Imitamos al cliente web: el servidor regional las espera.
            "Origin": "https://web.plaud.ai",
            "Referer": "https://web.plaud.ai/",
            "app-platform": "web",
        })

    @classmethod
    def from_env(cls, var: str = "PLAUD_TOKEN") -> "PlaudClient":
        """El token se lee del entorno y nunca se escribe en disco ni en logs."""
        token = os.environ.get(var, "")
        if not token:
            raise PlaudError(
                f"Falta {var}. Cárgalo con: read -rs {var} && export {var}"
            )
        return cls(token)

    def _get(self, path: str) -> dict:
        r = self.session.get(self.api + path, timeout=30)
        if r.status_code == 401:
            raise PlaudError("Token rechazado (401): cópialo de nuevo de web.plaud.ai.")
        r.raise_for_status()
        body = r.json()
        # Plaud reparte cuentas por región: si llamamos al servidor equivocado
        # responde -302 con el dominio correcto. Lo adoptamos y repetimos.
        if body.get("status") == -302:
            new_api = body["data"]["domains"]["api"].rstrip("/")
            if new_api == self.api:  # evita bucle infinito
                raise PlaudError(f"Redirección de región circular a {new_api}")
            logger.info("Región Plaud: %s", new_api)
            self.api = new_api
            return self._get(path)
        if body.get("status") != 0:
            raise PlaudError(f"Plaud respondió status={body.get('status')} msg={body.get('msg')}")
        return body

    def iter_files(self):
        """Todas las grabaciones (no papelera), de la más antigua a la más
        reciente, paginando de PAGE_SIZE en PAGE_SIZE."""
        skip = 0
        while True:
            page = self._get(
                f"/file/simple/web?skip={skip}&limit={PAGE_SIZE}"
                "&is_trash=2&sort_by=start_time&is_desc=false"
            ).get("data_file_list") or []
            yield from page
            if len(page) < PAGE_SIZE:
                return
            skip += PAGE_SIZE

    def folders(self) -> dict[str, str]:
        """{id de carpeta: nombre}. Verificado: GET /filetag/ (sonda 2026-10-14)."""
        tags = self._get("/filetag/").get("data_filetag_list") or []
        return {str(t["id"]): t.get("name") or str(t["id"]) for t in tags}

    def file_detail(self, file_id: str) -> dict:
        return self._get(f"/file/detail/{file_id}").get("data") or {}

    def audio_url(self, file_id: str) -> str | None:
        """URL temporal (S3 firmada, caduca) del mp3 original."""
        return self._get(f"/file/temp-url/{file_id}").get("temp_url")

    @staticmethod
    def fetch(url: str) -> bytes:
        """Descarga un recurso de S3. Las URLs firmadas ya llevan la
        autorización en la query: NO enviamos nuestro token a Amazon."""
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        data = r.content
        # Algunos contenidos (.gz) llegan comprimidos sin Content-Encoding.
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data

    @classmethod
    def fetch_text(cls, url: str) -> str:
        return cls.fetch(url).decode("utf-8")

    @classmethod
    def fetch_json_or_text(cls, url: str):
        text = cls.fetch_text(url)
        try:
            return json.loads(text)
        except ValueError:
            return text
