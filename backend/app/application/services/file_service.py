from typing import Dict, Any, Optional, BinaryIO, Tuple
import logging
import os
import re
import tempfile
from app.domain.external.file import FileStorage
from app.domain.models.file import FileInfo
from app.application.services.token_service import TokenService
from app.application.errors.exceptions import ValidationError
from app.core.config import get_settings

# Set up logger
logger = logging.getLogger(__name__)

class FileService:
    def __init__(self, file_storage: Optional[FileStorage] = None, token_service: Optional[TokenService] = None):
        self._file_storage = file_storage
        self._token_service = token_service

    async def upload_file(
        self,
        file_data: BinaryIO,
        filename: str,
        user_id: str,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FileInfo:
        """Validate and upload a bounded file stream."""
        logger.info(
            "Upload file request: filename=%s, user_id=%s, content_type=%s",
            filename,
            user_id,
            content_type,
        )
        if not self._file_storage:
            logger.error("File storage service not available")
            raise RuntimeError("File storage service not available")

        settings = get_settings()
        raw_filename = (filename or "upload").replace("\x00", "")
        safe_filename = os.path.basename(raw_filename.replace("\\", "/")).strip()
        safe_filename = re.sub(r"[\r\n]+", "_", safe_filename)
        if not safe_filename or safe_filename in {".", ".."}:
            safe_filename = "upload"
        safe_filename = safe_filename[: settings.max_filename_length]

        safe_content_type = (content_type or "application/octet-stream").strip().lower()
        if any(ord(char) < 32 for char in safe_content_type):
            raise ValidationError("Invalid content type")

        # Copy through a spooled temporary file so size is enforced before the
        # storage adapter receives any bytes. It rolls to disk above the memory threshold.
        total = 0
        with tempfile.SpooledTemporaryFile(max_size=5 * 1024 * 1024, mode="w+b") as bounded_file:
            while True:
                chunk = file_data.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise ValidationError(
                        f"File exceeds the {settings.max_upload_bytes} byte upload limit"
                    )
                bounded_file.write(chunk)
            bounded_file.seek(0)

            # User-supplied metadata cannot override ownership or storage fields.
            safe_metadata = {
                key: value
                for key, value in (metadata or {}).items()
                if key not in {"user_id", "filename", "uploadDate", "contentType"}
            }
            result = await self._file_storage.upload_file(
                bounded_file,
                safe_filename,
                user_id,
                safe_content_type,
                safe_metadata,
            )

        logger.info("File uploaded successfully: file_id=%s, user_id=%s", result.file_id, user_id)
        return result
    
    async def download_file(self, file_id: str, user_id: Optional[str] = None) -> Tuple[BinaryIO, FileInfo]:
        """Download file"""
        logger.info(f"Download file request: file_id={file_id}, user_id={user_id}")
        if not self._file_storage:
            logger.error("File storage service not available")
            raise RuntimeError("File storage service not available")
        
        try:
            result = await self._file_storage.download_file(file_id, user_id)
            logger.info(f"File downloaded successfully: file_id={file_id}, user_id={user_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to download file {file_id} for user {user_id}: {str(e)}")
            raise

    async def delete_file(self, file_id: str, user_id: str) -> bool:
        """Delete file"""
        logger.info(f"Delete file request: file_id={file_id}, user_id={user_id}")
        if not self._file_storage:
            logger.error("File storage service not available")
            raise RuntimeError("File storage service not available")
        
        try:
            result = await self._file_storage.delete_file(file_id, user_id)
            if result:
                logger.info(f"File deleted successfully: file_id={file_id}, user_id={user_id}")
            else:
                logger.warning(f"File deletion failed or file not found: file_id={file_id}, user_id={user_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to delete file {file_id} for user {user_id}: {str(e)}")
            raise

    async def get_file_info(self, file_id: str, user_id: Optional[str] = None) -> Optional[FileInfo]:
        """Get file information"""
        logger.info(f"Get file info request: file_id={file_id}, user_id={user_id}")
        if not self._file_storage:
            logger.error("File storage service not available")
            raise RuntimeError("File storage service not available")
        
        try:
            result = await self._file_storage.get_file_info(file_id, user_id)
            if result:
                logger.info(f"File info retrieved successfully: file_id={file_id}, user_id={user_id}")
            else:
                logger.warning(f"File not found or access denied: file_id={file_id}, user_id={user_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to get file info {file_id} for user {user_id}: {str(e)}")
            raise
    
    async def enrich_with_file_url(self, file_info: FileInfo) -> FileInfo:
        """Enrich file information with file URL"""
        logger.info(f"Enrich file info request: file_info={file_info}")
        
        try:
            signed_url = await self.create_signed_url(file_info.file_id, file_info.user_id)
            file_info.file_url = signed_url
            return file_info
        except Exception as e:
            logger.error(f"Failed to enrich file info {file_info.file_id} with file URL: {str(e)}")
            raise

    async def create_signed_url(self, file_id: str, user_id: Optional[str] = None, expire_minutes: int = 15) -> str:
        """Create signed URL for file download"""
        logger.info(f"Create signed URL request: file_id={file_id}, user_id={user_id}, expire_minutes={expire_minutes}")
        
        if not self._token_service:
            logger.error("Token service not available")
            raise RuntimeError("Token service not available")
        
        # Enforce the same bounded TTL as the API schema.
        if expire_minutes < 1:
            raise ValueError("expire_minutes must be at least 1")
        expire_minutes = min(expire_minutes, 15)
        
        # Check if file exists and user has access
        file_info = await self.get_file_info(file_id, user_id)
        if not file_info:
            logger.warning(f"File not found or access denied for signed URL: file_id={file_id}, user_id={user_id}")
            raise FileNotFoundError("File not found")
        
        # Create signed URL for file download
        base_url = f"/api/v1/files/{file_id}"
        signed_url = self._token_service.create_signed_url(
            base_url=base_url,
            expire_minutes=expire_minutes
        )
        
        logger.info(f"Created signed URL for file download for user {user_id}, file {file_id}")
        
        return signed_url
