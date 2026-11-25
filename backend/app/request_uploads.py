import re
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from starlette.datastructures import UploadFile

from app.database import BACKEND_DIR
from app.models import RequestAttachment

MAX_PHOTOS = 5
MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
INPUT_FORMATS = [*IMAGE_TYPES, "BMP", "GIF", "TIFF", "AVIF", "HEIF"]
register_heif_opener(thumbnails=False, depth_images=False, aux_images=False, decode_threads=2)


def get_upload_directory() -> Path:
    return BACKEND_DIR / "uploads"


def attachment_path(directory: Path, stored_filename: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", stored_filename):
        raise HTTPException(404, "Attachment not found.")
    path = directory.resolve() / stored_filename
    if path.is_symlink():
        raise HTTPException(404, "Attachment not found.")
    return path


def attachment_response(directory: Path, attachment: RequestAttachment | None) -> FileResponse:
    if attachment is None or attachment.content_type not in IMAGE_TYPES.values():
        raise HTTPException(404, "Attachment not found.")
    path = attachment_path(directory, attachment.stored_filename)
    if not path.is_file():
        raise HTTPException(404, "Attachment not found.")
    return FileResponse(path, media_type=attachment.content_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


def display_filename(filename: str | None) -> str:
    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(char for char in basename if char.isprintable())[:255].strip() or "Photo"


def normalize_photo(upload: UploadFile) -> tuple[bytes, str]:
    if upload.size is not None and upload.size > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Each photo must be 15 MiB or smaller.")

    image_data = upload.file.read(MAX_PHOTO_BYTES + 1)
    if len(image_data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Each photo must be 15 MiB or smaller.")

    try:
        with Image.open(BytesIO(image_data), formats=INPUT_FORMATS) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise HTTPException(413, "Each photo must contain at most 16 megapixels.")
            if source.format in IMAGE_TYPES and getattr(source, "is_animated", False):
                raise HTTPException(422, "Upload a still JPEG, PNG, or WebP photo.")
            image_format = source.format
            source.verify()
        with Image.open(BytesIO(image_data), formats=[image_format]) as source:
            source.load()
            has_alpha = "A" in source.getbands() or "transparency" in source.info
            storage_format = image_format if image_format in IMAGE_TYPES else "WEBP"
            mode = "RGBA" if has_alpha and storage_format != "JPEG" else "RGB"
            with ImageOps.exif_transpose(source) as oriented, oriented.convert(mode) as photo, BytesIO() as output:
                photo.info.clear()
                photo.save(output, format=storage_format)
                if output.tell() > MAX_PHOTO_BYTES:
                    raise HTTPException(413, "The processed photo exceeds 15 MiB. Choose a smaller image.")
                return output.getvalue(), IMAGE_TYPES[storage_format]
    except Image.DecompressionBombError:
        raise HTTPException(413, "Each photo must contain at most 16 megapixels.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, TypeError, EOFError, OverflowError):
        raise HTTPException(422, "Use a valid JPEG, PNG, WebP, BMP, GIF, TIFF, AVIF or HEIC image.") from None
