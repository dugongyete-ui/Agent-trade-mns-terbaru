from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import get_settings
from app.domain.services.agents import thinking_state
from app.interfaces.dependencies import get_current_user
from app.interfaces.schemas.base import APIResponse
from app.interfaces.schemas.config import ClientConfigResponse
from app.domain.models.user import User

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/frontend", response_model=APIResponse[ClientConfigResponse])
async def get_frontend_config() -> APIResponse[ClientConfigResponse]:
    """Get frontend runtime config."""
    settings = get_settings()

    return APIResponse.success(
        ClientConfigResponse(
            auth_provider=settings.auth_provider,
            google_analytics_id=settings.google_analytics_id,
        )
    )


class ThinkingModeResponse(BaseModel):
    thinking_mode: str  # "on" | "off"


class ThinkingModeRequest(BaseModel):
    enabled: bool


@router.get("/thinking", response_model=APIResponse[ThinkingModeResponse])
async def get_thinking_mode(
    current_user: User = Depends(get_current_user),
) -> APIResponse[ThinkingModeResponse]:
    """Current thinking (reasoning) mode — default comes from THINKING_MODE env."""
    return APIResponse.success(
        ThinkingModeResponse(
            thinking_mode="on" if thinking_state.is_enabled() else "off"
        )
    )


@router.put("/thinking", response_model=APIResponse[ThinkingModeResponse])
async def set_thinking_mode(
    request: ThinkingModeRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[ThinkingModeResponse]:
    """Toggle thinking mode at runtime (UI switch). Applies to all new LLM calls."""
    value = thinking_state.set_enabled(request.enabled)
    return APIResponse.success(
        ThinkingModeResponse(thinking_mode="on" if value else "off")
    )
