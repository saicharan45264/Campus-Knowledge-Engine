"""backend/services/storage_service.py — Image storage service for cropped diagrams.

Handles uploading cropped question diagrams to Cloudinary when configured,
with an automatic fallback to local disk storage (/uploads/images/) and
relative URL serving (/static/images/filename.jpg).
"""
from __future__ import annotations

import io
import logging
import os
import re

from backend.config import settings

logger = logging.getLogger("mach_ai.storage")

try:
    import cloudinary
    import cloudinary.api
    import cloudinary.uploader

    CLOUDINARY_CLOUD_NAME = settings.cloudinary_cloud_name
    CLOUDINARY_API_KEY = settings.cloudinary_api_key
    CLOUDINARY_API_SECRET = settings.cloudinary_api_secret
    if CLOUDINARY_CLOUD_NAME:
        cloudinary.config(
            cloud_name=CLOUDINARY_CLOUD_NAME,
            api_key=CLOUDINARY_API_KEY,
            api_secret=CLOUDINARY_API_SECRET,
            secure=True,
        )
except ImportError:
    cloudinary = None
    CLOUDINARY_CLOUD_NAME = ""


def upload_question_image_to_cloudinary(image_bytes: bytes, public_id: str) -> str:
    """Upload a cropped question image to Cloudinary or fallback to local disk storage."""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", public_id)

    if cloudinary and CLOUDINARY_CLOUD_NAME:
        try:
            buf = io.BytesIO(image_bytes)
            result = cloudinary.uploader.upload(
                buf,
                public_id=f"campus_ke/{safe_id}",
                resource_type="image",
                overwrite=True,
                transformation=[{"fetch_format": "auto", "quality": "auto"}],
            )
            url = result.get("secure_url", "")
            if url:
                return url
        except Exception as e:
            logger.info("Cloudinary upload failed (%s), falling back to local disk.", e)

    # Local disk fallback (sanitized filename to prevent directory traversal)
    filename = f"{safe_id}.jpg"
    os.makedirs(settings.images_dir, exist_ok=True)
    local_path = os.path.join(settings.images_dir, filename)
    with open(local_path, "wb") as f:
        f.write(image_bytes)
    return f"/static/images/{filename}"


def delete_document_images_from_cloudinary(document_id: str) -> None:
    """Delete all Cloudinary resources starting with the document prefix."""
    if cloudinary and CLOUDINARY_CLOUD_NAME:
        try:
            prefix = f"campus_ke/{document_id}"
            cloudinary.api.delete_resources_by_prefix(prefix)
        except Exception as e:
            logger.warning("Failed to delete Cloudinary resources for doc %s: %s", document_id, e)


def delete_all_images_from_cloudinary() -> None:
    """Delete all Cloudinary resources under campus_ke/ namespace."""
    if cloudinary and CLOUDINARY_CLOUD_NAME:
        try:
            prefix = "campus_ke/"
            cloudinary.api.delete_resources_by_prefix(prefix)
        except Exception as e:
            logger.warning("Failed to wipe Cloudinary resources: %s", e)

