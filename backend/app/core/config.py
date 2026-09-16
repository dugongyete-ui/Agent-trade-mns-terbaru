import os
import json
from pathlib import Path
import logging
from pydantic import Field
from pydantic_settings import BaseSettings
from functools import lru_cache

logger = logging.getLogger(__name__)


def _parse_extra_headers() -> dict | None:
    raw = os.environ.get("EXTRA_HEADERS")
    if not raw:
        return None
    try:
        headers = json.loads(raw)
        if isinstance(headers, dict):
            return headers
        logger.warning("EXTRA_HEADERS is not a JSON object, ignoring")
    except json.JSONDecodeError:
        logger.warning("EXTRA_HEADERS is not valid JSON, ignoring")
    return None


class Settings(BaseSettings):
    
    # Runtime environment
    environment: str = "development"

    # Model provider configuration
    api_key: str | None = None
    api_base: str | None = None
    
    # Model configuration
    model_name: str = "qwen3.7-max"
    model_provider: str = "openai"
    temperature: float = 0.7
    max_tokens: int | None = None
    
    # MongoDB configuration
    mongodb_uri: str = "mongodb://mongodb:27017"
    mongodb_database: str = "dzeck"
    mongodb_username: str | None = None
    mongodb_password: str | None = None
    
    # Redis configuration
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    
    # Vision model configuration (optional)
    vision_model_name: str | None = None
    vision_model_provider: str | None = None
    vision_api_base: str | None = None
    vision_api_key: str | None = None

    # Summary model configuration (optional, for session title generation)
    summary_model_name: str | None = None

    # Planner model (optional separate model for planning vs execution)
    planner_model_name: str | None = None
    planner_model_provider: str | None = None
    planner_api_base: str | None = None
    planner_api_key: str | None = None

    # Thinking (reasoning) mode — env var: THINKING_MODE ("on"/"off", default on)
    # When on, reasoning models (nvidia/nemotron) emit chain-of-thought that is
    # streamed to the web UI as a collapsible block. When off, the model is
    # explicitly told NOT to reason (chat_template_kwargs.thinking=false) and
    # answers directly — faster and cheaper. Runtime-switchable via
    # GET/PUT /api/v1/config/thinking.
    thinking_mode: bool = True

    # Agent step limit — env var: MAX_STEPS
    # None (default) = UNLIMITED. The agent runs until the plan is complete,
    # blocked only by max_consecutive_failures and the user's stop button.
    max_steps: int | None = Field(default=None, alias="max_steps", ge=1)

    # How many consecutive failed steps before the loop skips to SUMMARIZING.
    # Increase if tasks involve many optional tool calls that may legitimately fail.
    # env var: MAX_CONSECUTIVE_FAILURES
    max_consecutive_failures: int = Field(default=2, alias="max_consecutive_failures", ge=1, le=20)

    # Agent behavior
    conversation_save_path: str | None = None    # dir to save conversation logs, e.g. "/tmp/conversations"
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1, le=250 * 1024 * 1024)
    max_attachment_extract_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    max_attachment_extract_chars: int = Field(default=120_000, ge=1_000, le=1_000_000)
    max_filename_length: int = Field(default=255, ge=32, le=1024)
    share_token_expire_days: int = Field(default=7, ge=1, le=30)
    extend_system_message: str | None = None     # extra instructions appended to all agent system prompts

    # Cross-session persistent memory (MongoDB-backed, per user).
    # env var: MEMORY_ENABLED — when false, the remember tool is not registered
    # and no recalled-memory block is injected into messages.
    memory_enabled: bool = True

    # Grounding gate — mechanical anti-hallucination check on the final summary
    # (env var: GROUNDING_ENABLED). Every decimal/percentage figure must match
    # a value some tool returned this run, be a declared `derived`/`proposed`
    # figure with visible arithmetic, or be a visibly `cited` source. Failed
    # drafts get bounded correction rounds; a still-failing draft is released
    # with unverifiable figures redacted. Never blocks forever, fail-open.
    grounding_enabled: bool = True

    # Search engine configuration
    search_provider: str | None = "tavily"
    tavily_api_key: str | None = None
    
    # Google Analytics configuration
    google_analytics_id: str | None = None

    # CORS configuration — comma-separated list of allowed origins, or "*" to allow all
    # Example: ALLOWED_ORIGINS=https://yourapp.replit.app,https://yourdomain.com
    allowed_origins: str = "*"

    # Auth configuration
    auth_provider: str = "password"  # "password", "none", "local"
    password_salt: str | None = None
    password_hash_rounds: int = 600000
    password_hash_algorithm: str = "pbkdf2_sha256"
    local_auth_email: str = "admin@example.com"
    local_auth_password: str = "admin"
    
    # Email configuration
    email_host: str | None = None  # "smtp.gmail.com"
    email_port: int | None = None  # 587
    email_username: str | None = None
    email_password: str | None = None
    email_from: str | None = None
    
    # JWT configuration
    jwt_secret_key: str = "your-secret-key-here"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7
    
    # Extra headers for LLM requests (parsed from EXTRA_HEADERS env var, JSON)
    extra_headers: dict | None = None
    
    # SSL verification — set SSL_VERIFY=false only for custom gateways with self-signed certs
    ssl_verify: bool = True

    # MCP configuration
    mcp_config_path: str = str(Path(__file__).resolve().parents[3] / "mcp.json")
    
    # Logging configuration
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        
    def check_required_settings(self):
        """Validate configuration settings and reject unsafe production defaults."""
        if not self.api_key:
            raise ValueError("API key is required")

        environment = self.environment.strip().lower()
        production = environment in {"production", "prod"}
        if self.jwt_secret_key == "your-secret-key-here":
            message = "JWT_SECRET_KEY is using the default insecure value."
            if production:
                raise ValueError(message + " Set a strong random secret before production startup.")
            logger.warning(message + " Set JWT_SECRET_KEY outside development.")

        if production and self.auth_provider == "none":
            raise ValueError("AUTH_PROVIDER=none is not allowed in production")
        if production and self.allowed_origins.strip() == "*":
            raise ValueError("ALLOWED_ORIGINS=* is not allowed in production")
        if production and self.auth_provider == "local" and self.local_auth_password == "admin":
            raise ValueError("Default local authentication password is not allowed in production")
        if production and not (self.password_salt or "").strip() and self.auth_provider == "password":
            raise ValueError("PASSWORD_SALT is required for password authentication in production")

@lru_cache()
def get_settings() -> Settings:
    """Get application settings"""
    if not os.environ.get("OPENAI_API_KEY"):
        api_key_val = os.getenv("API_KEY")
        if api_key_val:
            os.environ["OPENAI_API_KEY"] = api_key_val
    settings = Settings()
    settings.extra_headers = _parse_extra_headers()
    settings.check_required_settings()
    return settings 
