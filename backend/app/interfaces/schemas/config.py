from pydantic import BaseModel


class ClientConfigResponse(BaseModel):
    """Client runtime configuration response schema"""
    auth_provider: str
    google_analytics_id: str | None = None

