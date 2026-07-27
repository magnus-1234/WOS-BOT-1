import os
import sys
import logging
from datetime import datetime
from fastapi import FastAPI, APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# Safety import for mongo
try:
    from db.mongo_adapters import mongo_enabled
except ImportError:
    mongo_enabled = lambda: False

logger = logging.getLogger(__name__)
STARTED_AT = datetime.utcnow()

app = FastAPI(
    title="Whiteout Survival Bot API",
    description="The modular API powering the web dashboard.",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://whiteout-survival-bot.vercel.app",
        "https://whiteout-survival.vercel.app",
        "https://wos-bot-dashboard.vercel.app",
        "http://wos-bot-dashboard.vercel.app",
        "http://localhost:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:5173",  # Vite default
        "http://127.0.0.1:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler to capture 500 errors
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"🔥 Global API Error at {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "message": str(exc),
            "path": request.url.path
        },
    )

# Exception handler for validation errors to help debug 422s
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"❌ API Validation Error at {request.url.path}: {exc.errors()}")
    logger.error(f"   Payload: {await request.body()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": str(await request.body())},
    )

@app.get("/")
@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {
        "status": "online",
        "timestamp": datetime.utcnow().isoformat(),
        "bot_loaded": hasattr(app.state, "bot") and app.state.bot is not None
    }

@app.get("/api/status")
@app.get("/status")
async def get_status():
    bot = getattr(app.state, "bot", None)
    now = datetime.utcnow()
    bot_ready_at = getattr(bot, "ready_at", None) if bot else None
    uptime_started_at = bot_ready_at if isinstance(bot_ready_at, datetime) else STARTED_AT
    uptime_seconds = int((now - uptime_started_at).total_seconds())
    guilds = list(bot.guilds) if bot else []
    total_members = sum((guild.member_count or 0) for guild in guilds)
    commands_count = 0
    if bot:
        try:
            commands_count = len(bot.tree.get_commands())
        except Exception:
            commands_count = 0
    return {
        "status": "online" if bot and getattr(bot, "is_ready", lambda: False)() else "starting",
        "bot_id": str(bot.user.id) if bot and bot.user else None,
        "bot_name": bot.user.name if bot and bot.user else "Whiteout Survival Bot",
        "bot_avatar": str(bot.user.avatar.url) if bot and bot.user and bot.user.avatar else None,
        "guilds_count": len(guilds),
        "servers_count": len(guilds),
        "members_count": total_members,
        "total_members": total_members,
        "commands_count": commands_count,
        "uptime_seconds": uptime_seconds,
        "started_at": uptime_started_at.isoformat(),
        "api_started_at": STARTED_AT.isoformat(),
        "bot_ready_at": bot_ready_at.isoformat() if isinstance(bot_ready_at, datetime) else None,
        "latency_ms": round(bot.latency * 1000) if (bot and bot.latency != float('inf')) else None,
        "bot_feed_loaded": True,
        "activity_store_enabled": mongo_enabled(),
    }

@app.get("/api/debug/env")
@app.get("/debug/env")
async def debug_env():
    return {
        "status": "ok",
        "port": os.environ.get("PORT", "8080 (default)"),
        "mongo_enabled": mongo_enabled(),
        "discord_client_configured": bool(os.getenv("DISCORD_CLIENT_ID")),
        "redirect_uri_set": bool(os.getenv("OAUTH_REDIRECT_URI")),
        "platform": sys.platform,
        "python_version": sys.version
    }

# Safe router imports
try:
    from web_api.routers.auth import router as auth_router
    app.include_router(auth_router)
    logger.info("✅ Auth router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load auth router: {e}")

try:
    from web_api.routers.giftcodes import router as giftcodes_router
    app.include_router(giftcodes_router, prefix="/api/giftcodes")
    logger.info("✅ Giftcodes router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load giftcodes router: {e}")

try:
    from web_api.routers.servers import router as servers_router
    app.include_router(servers_router)
    logger.info("✅ Servers router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load servers router: {e}")

try:
    from web_api.routers.guilds import router as guilds_router
    app.include_router(guilds_router)
    logger.info("✅ Guilds router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load guilds router: {e}")

try:
    from web_api.routers.settings import router as settings_router
    app.include_router(settings_router)
    logger.info("✅ Settings router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load settings router: {e}")

try:
    from web_api.routers.reminders import router as reminders_router
    app.include_router(reminders_router)
    logger.info("✅ Reminders router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load reminders router: {e}")

try:
    from web_api.routers.alliance_monitor import router as alliance_monitor_router
    app.include_router(alliance_monitor_router)
    logger.info("✅ Alliance Monitor router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load alliance monitor router: {e}")

try:
    from web_api.routers.bot_feed import router as bot_feed_router
    app.include_router(bot_feed_router)
    logger.info("✅ Bot Feed router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load bot feed router: {e}")

try:
    from web_api.routers.chat import router as chat_router
    app.include_router(chat_router)
    logger.info("✅ Global Chat router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load global chat router: {e}")

try:
    try:
        from web_api.routers.message_templates import router as message_templates_router
    except Exception:
        from src.api.routers.message_templates import router as message_templates_router
    app.include_router(message_templates_router)
    logger.info("✅ Message Templates router loaded")
except Exception as e:
    logger.error(f"❌ Failed to load message templates router: {e}")

os.makedirs("data/uploads", exist_ok=True)
app.mount("/api/static", StaticFiles(directory="data/uploads"), name="static")

async def start_web_server(bot=None, port: int = None):
    # Use provided port or environment variable or default to 8080
    if port is None:
        port = int(os.environ.get("PORT", 8080))
        
    app.state.bot = bot
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=port,
        loop="asyncio",
        log_level="info"
    )
    server = uvicorn.Server(config)
    logger.info(f"🚀 FastAPI Web Server starting on port {port}")
    await server.serve()

