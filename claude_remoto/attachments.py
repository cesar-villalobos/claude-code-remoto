"""Capturas y archivos de texto adjuntos a una tarea: se validan y se envían a Claude sin guardarse.

En el historial solo queda una miniatura pequeña de cada captura (la genera el navegador) y, de
los archivos de texto, su nombre y tamaño: el contenido va solo a Claude.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
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


# -- archivos de texto (logs, código, configuración…) -------------------------
MAX_TEXT_FILES = 5
MAX_TEXT_BYTES = 200 * 1024      # por archivo
MAX_TEXT_TOTAL = 500 * 1024      # entre todos
MAX_NAME = 100
# Nunca se adjuntan archivos de secretos, aunque se elijan por error
SECRET_NAME = re.compile(r"(^\.env(\..*)?$|\.(pem|key|p12|pfx|jks|keystore|kdbx)$|^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$|"
                         r"^\.?netrc$|^\.npmrc$|^\.pypirc$|^credentials(\.json)?$)", re.I)


def clean_name(value: Any) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", os.path.basename(str(value or "").replace("\\", "/"))).strip()
    return name[:MAX_NAME] or "archivo.txt"


def validate_texts(files: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
    """[{name, text}] → (archivos listos, error)."""
    if files in (None, []):
        return [], None
    if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
        return None, "Formato de archivos no válido"
    if len(files) > MAX_TEXT_FILES:
        return None, f"Máximo {MAX_TEXT_FILES} archivos de texto por tarea"
    out, total = [], 0
    for f in files:
        name = clean_name(f.get("name"))
        text = f.get("text")
        if not isinstance(text, str):
            return None, f"«{name}»: el contenido no es texto"
        if SECRET_NAME.search(name):
            return None, f"«{name}» parece un archivo de secretos: no se adjunta"
        if "\x00" in text:
            return None, f"«{name}» no es un archivo de texto"
        size = len(text.encode("utf-8"))
        if size > MAX_TEXT_BYTES:
            return None, f"«{name}» supera los {MAX_TEXT_BYTES // 1024} KB"
        total += size
        if total > MAX_TEXT_TOTAL:
            return None, f"Los archivos suman más de {MAX_TEXT_TOTAL // 1024} KB"
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        out.append({"name": name, "text": text, "bytes": size, "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0)})
    return out, None


def files_meta(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lo que queda en el historial y la auditoría: nombre, tamaño y líneas (nunca el contenido)."""
    return [{"name": f["name"], "bytes": f["bytes"], "lines": f["lines"]} for f in files]


def with_files(text: str, files: list[dict[str, Any]]) -> str:
    """El mensaje para Claude con cada archivo en su propio bloque de código."""
    if not files:
        return text
    parts = [text]
    for f in files:
        body = f["text"].rstrip("\n")
        fence = "`" * max(3, max((len(m) for m in re.findall(r"`{3,}", body)), default=0) + 1)
        ext = os.path.splitext(f["name"])[1].lstrip(".").lower()
        lang = ext if re.fullmatch(r"[a-z0-9+#-]{1,10}", ext or "") else ""
        parts.append(f"Archivo adjunto «{f['name']}» ({f['lines']} líneas):\n{fence}{lang}\n{body}\n{fence}")
    return "\n\n".join(parts)
