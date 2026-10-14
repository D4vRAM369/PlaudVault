"""transcriber.py — Audio → texto (faster-whisper) y texto → texto (passthrough).

Por qué el import de faster_whisper es "perezoso" (dentro de la función y
no arriba del fichero): faster-whisper arrastra ctranslate2, que pesa
cientos de MB y tarda en cargar. Si solo vas a ingerir un transcript .txt
o hacer una búsqueda, no tiene sentido pagar ese coste. El modelo se carga
la primera vez que llega un audio, y se cachea para el resto de la sesión.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("plaudvault.transcriber")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".opus", ".ogg", ".flac", ".aac"}
TEXT_EXTENSIONS = {".txt", ".md"}


@dataclass
class TranscriptionResult:
    text: str
    language: str
    model: str
    duration_secs: float | None  # None cuando la fuente es texto


class Transcriber:
    def __init__(
        self,
        model_name: str = "small",
        language: str = "es",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_name = model_name
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._model = None  # se carga perezosamente

    def _load_model(self):
        if self._model is None:
            # Import aquí dentro a propósito — ver docstring del módulo.
            from faster_whisper import WhisperModel

            logger.info(
                "Cargando faster-whisper '%s' (%s/%s)…",
                self.model_name, self.device, self.compute_type,
            )
            self._model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def transcribe(self, path: Path) -> TranscriptionResult:
        """Punto de entrada único: decide según la extensión."""
        ext = path.suffix.lower()
        if ext in TEXT_EXTENSIONS:
            return self._passthrough(path)
        if ext in AUDIO_EXTENSIONS:
            return self._transcribe_audio(path)
        raise ValueError(f"Extensión no soportada: {ext}")

    def _passthrough(self, path: Path) -> TranscriptionResult:
        """El fichero YA es un transcript (export de Plaud Note, p. ej.).
        No re-transcribimos: el valor de PlaudVault está en archivar,
        indexar y cifrar — no en repetir trabajo ya hecho."""
        return TranscriptionResult(
            text=path.read_text(encoding="utf-8"),
            language=self.language,
            model="passthrough",
            duration_secs=None,
        )

    def _transcribe_audio(self, path: Path) -> TranscriptionResult:
        model = self._load_model()
        # segments es un generador: faster-whisper transcribe a trozos
        # (como subtítulos) y los vamos consumiendo según se producen.
        segments, info = model.transcribe(
            str(path), language=self.language, vad_filter=True
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return TranscriptionResult(
            text=text,
            language=info.language or self.language,
            model=f"faster-whisper-{self.model_name}",
            duration_secs=round(info.duration, 2) if info.duration else None,
        )
