from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import logging
import asyncio
import os

from app.core.config import get_settings
from app.infrastructure.storage.mongodb import get_mongodb
from app.infrastructure.storage.redis import get_redis
from app.interfaces.dependencies import get_agent_service
from app.interfaces.api.routes import router
from app.infrastructure.logging import setup_logging
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.infrastructure.models.documents import AgentDocument, SessionDocument, UserDocument, MemoryDocument
from beanie import init_beanie

# Initialize logging system
setup_logging()
logger = logging.getLogger(__name__)

# Load configuration
settings = get_settings()

# Seed the runtime thinking (reasoning) flag from THINKING_MODE env default.
from app.domain.services.agents import thinking_state as _thinking_state
_thinking_state.init_from_settings()

# Readiness is true only after MongoDB/Beanie and Redis are both initialized.
_app_ready = False
_startup_error: str | None = None
_startup_task: asyncio.Task | None = None


async def _init_databases() -> None:
    """Initialize required services and publish an accurate readiness state."""
    global _app_ready, _startup_error
    try:
        logger.info("Background DB init — connecting to MongoDB…")
        await get_mongodb().initialize()
        await init_beanie(
            database=get_mongodb().client[settings.mongodb_database],
            document_models=[AgentDocument, SessionDocument, UserDocument, MemoryDocument],
        )
        logger.info("Successfully initialized Beanie")
    except Exception as exc:
        _startup_error = "database initialization failed"
        logger.error(f"MongoDB/Beanie initialization failed: {exc}")
        return

    try:
        await get_redis().initialize()
        logger.info("Successfully initialized Redis")
    except Exception as exc:
        _startup_error = "queue initialization failed"
        logger.error(f"Redis initialization failed: {exc}")
        return

    _app_ready = True
    logger.info("Application fully ready — all services initialized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup - Dzeck AI Agent initializing")

    # Start initialization in the background, while /health accurately reports
    # 503 until every required dependency is ready.
    global _startup_task, _app_ready
    _app_ready = False
    _startup_task = asyncio.create_task(_init_databases())

    try:
        yield
    finally:
        if _startup_task and not _startup_task.done():
            _startup_task.cancel()
            try:
                await _startup_task
            except asyncio.CancelledError:
                pass
        logger.info("Application shutdown - Dzeck AI Agent terminating")
        await get_mongodb().shutdown()
        await get_redis().shutdown()
        _startup_task = None

        logger.info("Cleaning up AgentService instance")
        try:
            await asyncio.wait_for(get_agent_service().shutdown(), timeout=30.0)
            logger.info("AgentService shutdown completed successfully")
        except asyncio.TimeoutError:
            logger.warning("AgentService shutdown timed out after 30 seconds")
        except Exception as exc:
            logger.error(f"Error during AgentService cleanup: {str(exc)}")


app = FastAPI(title="Dzeck AI Agent", lifespan=lifespan)

# Configure CORS — restrict origins via ALLOWED_ORIGINS env var in production
_raw_origins = settings.allowed_origins.strip()
_cors_origins: list[str] = (
    ["*"]
    if _raw_origins == "*"
    else [o.strip() for o in _raw_origins.split(",") if o.strip()]
)
if _cors_origins != ["*"]:
    logger.info(f"CORS restricted to: {_cors_origins}")
else:
    logger.warning(
        "CORS is open to all origins (ALLOWED_ORIGINS=*). "
        "Set ALLOWED_ORIGINS=https://yourapp.replit.app in production."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting for auth + chat endpoints (429 on abuse)
from app.interfaces.middleware.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# Register exception handlers
register_exception_handlers(app)

# Register routes
app.include_router(router, prefix="/api/v1")


# Health check — readiness is 503 until required dependencies are initialized.
@app.get("/health")
async def health_check():
    """Readiness probe for MongoDB/Beanie and Redis-backed agent execution."""
    if not _app_ready:
        return JSONResponse(
            {"status": "degraded", "ready": False, "error": _startup_error},
            status_code=503,
        )
    return {"status": "ok", "ready": True}


@app.get("/health/live")
async def liveness_check():
    """Liveness probe that only confirms the FastAPI process is responding."""
    return {"status": "ok"}


# Serve compiled Vue frontend in production (when frontend/dist exists)
_frontend_dist = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../frontend/dist")
)

if os.path.exists(_frontend_dist):
    _assets_dir = os.path.join(_frontend_dist, "assets")
    if os.path.exists(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="static-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        # Path traversal guard: normalize and confine the resolved path inside
        # the dist directory. Anything escaping it (../, absolute, symlink-ish
        # trickery) falls back to index.html instead of leaking files.
        target = os.path.normpath(os.path.join(_frontend_dist, full_path.lstrip("/")))
        if (
            full_path
            and os.path.isfile(target)
            and (target == _frontend_dist or target.startswith(_frontend_dist + os.sep))
        ):
            return FileResponse(target)
        return FileResponse(os.path.join(_frontend_dist, "index.html"))
else:
    @app.get("/", include_in_schema=False)
    async def health_root():
        return JSONResponse({"status": "ok", "ready": _app_ready, "msg": "Dzeck backend running — frontend not built yet"})
