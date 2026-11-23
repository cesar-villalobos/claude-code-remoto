"""Instrucciones por voz: transcripción local con Whisper (el audio no sale de tu PC).

Si faster-whisper no está instalado, la interfaz puede usar el dictado del navegador,
avisando antes de que el audio pasa por servidores de Google o Apple.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import tempfile
import time
from typing import Any

from .config import log

# auto: Whisper si está instalado, si no el navegador · whisper · browser · off
VOICE_ENGINE = os.getenv("VOICE_ENGINE", "auto").strip().lower()
# Modelo: tiny, base, small, medium… o la ruta a un modelo ya descargado (sin internet)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base").strip()
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "es").strip() or None
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip()
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8").strip()
MAX_AUDIO = int(float(os.getenv("VOICE_MAX_MB", "8")) * 1024 * 1024)
AUDIO_TYPES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/aac": ".aac",
               "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-m4a": ".m4a"}

_model: Any = None
_lock = asyncio.Lock()


def whisper_installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def engine() -> str:
    """Motor efectivo que la interfaz debe usar: whisper | browser | off."""
    if VOICE_ENGINE in ("off", "browser"):
        return VOICE_ENGINE
    if whisper_installed():
        return "whisper"
    if VOICE_ENGINE == "whisper":
        return "off"  # pedido explícitamente y no está: no se cae al navegador sin avisar
    return "browser"


def audio_suffix(content_type: str) -> str | None:
    return AUDIO_TYPES.get(content_type.split(";")[0].strip().lower())


def _load_model() -> Any:
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        t = time.time()
        log.info("🎤 Cargando el modelo de Whisper «%s» (la primera vez puede descargarse)…", WHISPER_MODEL)
        _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
        log.info("🎤 Whisper listo en %.1f s", time.time() - t)
    return _model


def decode_audio(path: str, sampling_rate: int = 16000) -> Any:
    """Decodifica el audio a un array mono float32 de 16 kHz, como lo espera Whisper.

    No se usa faster_whisper.audio.decode_audio porque llama a av.open(metadata_errors=…),
    argumento que las versiones nuevas de PyAV (las que traen wheels para Python 3.14)
    ya no aceptan: TypeError: open() got an unexpected keyword argument 'metadata_errors'.
    """
    import av
    import numpy as np

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    chunks = []
    with av.open(path, mode="r") as container:
        for frame in container.decode(audio=0):
            chunks.extend(f.to_ndarray() for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray() for f in resampler.resample(None))  # vaciar el buffer
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0


def transcribe_file(path: str) -> str:
    """Transcribe un archivo de audio (webm/opus de Chrome, mp4/aac de Safari…)."""
    audio = decode_audio(path)
    segments, _ = _load_model().transcribe(audio, language=WHISPER_LANGUAGE, vad_filter=True, beam_size=5)
    return " ".join(s.text.strip() for s in segments).strip()


async def transcribe(audio: bytes, content_type: str) -> str:
    suffix = audio_suffix(content_type) or ".webm"
    with tempfile.TemporaryDirectory(prefix="cr-voz-") as d:
        path = os.path.join(d, "audio" + suffix)
        with open(path, "wb") as f:
            f.write(audio)
        async with _lock:  # un audio a la vez: el modelo usa toda la CPU
            return await asyncio.to_thread(transcribe_file, path)
