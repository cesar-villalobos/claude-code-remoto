"""Capturas adjuntas a una tarea: se validan y se envían a Claude sin guardarse en disco.

En el historial solo queda una miniatura pequeña (la genera el navegador) para verla
en todos los dispositivos.
"""

from __future__ import annotations

import base64
import binascii
import os
from typing import Any

MAX_IMAGES = 5
MAX_IMAGE_BYTES = 5 * 1024 * 1024          # límite de la API de Claude por imagen
MAX_THUMB_BYTES = 40 * 1024
MAX_UPLOAD = int(float(os.getenv("ATTACH_MAX_MB", "20")) * 1024 * 1024)   # cuerpo total de /api/send
MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}


def _decode(data: Any) -> bytes | None:
    if not isinstance(data, str) or not data:
        return None
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None


def _is_image(raw: bytes, media_type: str) -> bool:
    if media_type == "image/webp" and raw[8:12] != b"WEBP":
        return False
    return any(raw.startswith(m) for m in MAGIC.get(media_type, ()))


def validate(images: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    """[{media_type, data, thumb}] → (imágenes listas, error)."""
    if images in (None, []):
        return [], None
    if not isinstance(images, list):
        return None, "Formato de imágenes no válido"
    if len(images) > MAX_IMAGES:
        return None, f"Máximo {MAX_IMAGES} imágenes por tarea"
    out = []
    for i, img in enumerate(images, 1):
        if not isinstance(img, dict):
            return None, "Formato de imágenes no válido"
        media_type = str(img.get("media_type") or "").lower()
        if media_type not in MAGIC:
            return None, f"Imagen {i}: formato no admitido (usa PNG, JPEG, GIF o WebP)"
        raw = _decode(img.get("data"))
        if raw is None or not _is_image(raw, media_type):
            return None, f"Imagen {i}: el contenido no es una imagen válida"
        if len(raw) > MAX_IMAGE_BYTES:
            return None, f"Imagen {i}: supera los 5 MB"
        thumb = _decode(img.get("thumb"))
        if thumb is not None and (len(thumb) > MAX_THUMB_BYTES or not _is_image(thumb, "image/jpeg")):
            thumb = None  # sin miniatura válida se muestra solo el ícono
        out.append({"media_type": media_type, "data": img["data"], "bytes": len(raw),
                    "thumb": base64.b64encode(thumb).decode() if thumb else None})
    return out, None


def content_blocks(text: str, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bloques del mensaje para Claude: imágenes primero y luego el texto."""
    blocks: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": i["data"]}}
        for i in images
    ]
    blocks.append({"type": "text", "text": text})
    return blocks
