from pydantic import BaseModel
from typing import ClassVar, Optional, Dict, Any
from datetime import datetime
from app.domain.models.file import FileInfo




class FileInfoResponse(BaseModel):
    """Redacted file info response schema safe for browser/public replay."""

    _PUBLIC_METADATA_KEYS: ClassVar[frozenset[str]] = frozenset({"description", "category", "source", "alt"})
    file_id: str
    filename: str
    content_type: Optional[str]
    size: Optional[int] = None
    upload_date: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]]
    file_url: Optional[str]

    @staticmethod
    async def from_file_info(file_info: FileInfo) -> "FileInfoResponse":
        from app.interfaces.dependencies import get_file_service
        file_service = get_file_service()
        safe_metadata = {
            key: value
            for key, value in (file_info.metadata or {}).items()
            if key in FileInfoResponse._PUBLIC_METADATA_KEYS
        }
        return FileInfoResponse(
            file_id=file_info.file_id,
            filename=file_info.filename or "file",
            content_type=file_info.content_type,
            size=file_info.size,
            upload_date=file_info.upload_date,
            metadata=safe_metadata or None,
            file_url=await file_service.create_signed_url(file_info.file_id)
        )
