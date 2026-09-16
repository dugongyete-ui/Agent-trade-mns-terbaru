from typing import List, Dict, Any
from pydantic import BaseModel


VISION_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
}


def is_vision_capable(content_type: str) -> bool:
    if not content_type:
        return False
    return content_type.lower().split(";")[0].strip() in VISION_MIME_TYPES


class VisionImage(BaseModel):
    content_type: str
    data: str


class Message(BaseModel):
    message: str = ""
    attachments: List[str] = []
    vision_images: List[VisionImage] = []
