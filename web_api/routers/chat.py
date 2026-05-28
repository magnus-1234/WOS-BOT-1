import json
import logging
import os
import re
import uuid
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from src.api.wos_api import fetch_player_info
from src.bot.admin_utils import format_furnace_level

try:
    from db.mongo_adapters import _get_db_main_async, mongo_enabled
except ImportError:
    _get_db_main_async = None
    mongo_enabled = lambda: False


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["Global Chat"])
CHAT_ADMIN_USER_IDS = {
    os.getenv("CHAT_ADMIN_USER_ID", "850786361572720661")
}
ROOM_STATE_STORE = Path("data/global_chat_room_state.json")


def _default_room_state() -> Dict[str, Any]:
    return {
        "is_blizzard_active": False,
        "announcement": None,
        "announcement_author": None,
        "announcement_updated_at": None,
    }


def _read_room_state() -> Dict[str, Any]:
    state = _default_room_state()
    if ROOM_STATE_STORE.exists():
        try:
            stored = json.loads(ROOM_STATE_STORE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                state.update({key: stored.get(key) for key in state.keys() if key in stored})
        except Exception as exc:
            logger.error("Failed to read %s: %s", ROOM_STATE_STORE, exc)
    return state


def _write_room_state(state: Dict[str, Any]) -> None:
    ROOM_STATE_STORE.parent.mkdir(parents=True, exist_ok=True)
    ROOM_STATE_STORE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_chat_admin(info: Optional[Dict[str, Any]]) -> bool:
    if not info:
        return False
    user_id = str(info.get("id") or "").strip()
    return user_id in CHAT_ADMIN_USER_IDS


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[WebSocket, Dict[str, Any]] = {}
        self.room_state: Dict[str, Any] = _read_room_state()
        self._last_chat_announcement: Optional[str] = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[websocket] = {
            "id": None,
            "name": "Connecting User",
            "avatar_url": None,
            "kind": "guest",
            "is_typing": False
        }

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            del self.active_connections[websocket]

    async def register_user(self, websocket: WebSocket, user_info: Dict[str, Any]):
        if websocket in self.active_connections:
            user_id = str(user_info.get("id") or "").strip()
            self.active_connections[websocket].update({
                "id": user_id,
                "name": user_info.get("name", "Guest Player"),
                "avatar_url": user_info.get("avatar_url"),
                "kind": user_info.get("kind", "guest"),
                "furnace_level": user_info.get("furnace_level"),
                "furnace_level_formatted": user_info.get("furnace_level_formatted"),
                "state_id": user_info.get("state_id"),
                "is_admin": user_id in CHAT_ADMIN_USER_IDS,
            })
            await self.broadcast_presence()
            await websocket.send_json({
                "type": "room_state",
                **self.room_state,
                "is_admin": self.active_connections[websocket]["is_admin"],
            })

    async def set_typing(self, websocket: WebSocket, is_typing: bool):
        if websocket in self.active_connections:
            self.active_connections[websocket]["is_typing"] = is_typing
            await self.broadcast_typing()

    async def broadcast(self, message: Dict[str, Any]):
        for ws in list(self.active_connections.keys()):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)

    async def broadcast_message(self, message: Dict[str, Any]):
        msg_data = message.get("message", {})
        target_id = msg_data.get("target_user_id")
        author = msg_data.get("author", {})
        sender_id = author.get("id") or author.get("name")
        
        for ws, info in list(self.active_connections.items()):
            try:
                # If it's a private message, only send to sender and target
                if target_id:
                    ws_user_id = info.get("id") or info.get("name")
                    if ws_user_id not in (target_id, sender_id):
                        continue
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)

    async def broadcast_presence(self):
        users = []
        seen = set()
        for info in self.active_connections.values():
            user_id = info.get("id") or info.get("name")
            if user_id and user_id not in seen:
                seen.add(user_id)
                users.append({
                    "id": info.get("id"),
                    "name": info.get("name"),
                    "avatar_url": info.get("avatar_url"),
                    "kind": info.get("kind"),
                    "furnace_level": info.get("furnace_level"),
                    "furnace_level_formatted": info.get("furnace_level_formatted"),
                    "state_id": info.get("state_id"),
                })
        
        await self.broadcast({
            "type": "presence",
            "online_count": len(users),
            "users": users
        })

    async def broadcast_typing(self):
        typing_users = []
        for info in self.active_connections.values():
            if info.get("is_typing") and info.get("id"):
                typing_users.append({
                    "id": info.get("id"),
                    "name": info.get("name")
                })
        
        await self.broadcast({
            "type": "typing",
            "users": typing_users
        })

    async def send_to_user(self, user_id: str, message: Dict[str, Any]):
        for ws, info in list(self.active_connections.items()):
            ws_user_id = str(info.get("id") or info.get("name") or "")
            if ws_user_id == str(user_id):
                try:
                    await ws.send_json(message)
                except Exception:
                    self.disconnect(ws)

    async def broadcast_room_state(self):
        await self.broadcast({"type": "room_state", **self.room_state})

    async def update_announcement(self, announcement: Optional[str], author: Optional[Dict[str, Any]] = None, publish_to_chat: bool = False):
        self.room_state["announcement"] = announcement or None
        self.room_state["announcement_author"] = (author or {}).get("name") if announcement else None
        self.room_state["announcement_updated_at"] = _utc_now_iso() if announcement else None
        _write_room_state(self.room_state)
        await self.broadcast({
            "type": "admin:announcement",
            "announcement": self.room_state["announcement"],
            "announcement_author": self.room_state["announcement_author"],
            "announcement_updated_at": self.room_state["announcement_updated_at"],
        })
        if publish_to_chat and announcement:
            dedupe_key = f"{announcement}|{(author or {}).get('id')}|{self.room_state['announcement_updated_at']}"
            if dedupe_key != self._last_chat_announcement:
                self._last_chat_announcement = dedupe_key
                await _create_announcement_message(announcement, author or {})


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")
            if event_type == "register":
                user_info = data.get("user", {})
                await manager.register_user(websocket, user_info)
            elif event_type == "typing":
                is_typing = data.get("is_typing", False)
                await manager.set_typing(websocket, is_typing)
            elif event_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif event_type == "admin:blizzard":
                if not _is_chat_admin(manager.active_connections.get(websocket)):
                    await websocket.send_json({"type": "admin:error", "message": "Admin access required."})
                    continue
                manager.room_state["is_blizzard_active"] = bool(data.get("is_frozen"))
                _write_room_state(manager.room_state)
                await manager.broadcast_room_state()
            elif event_type == "admin:announcement":
                if not _is_chat_admin(manager.active_connections.get(websocket)):
                    await websocket.send_json({"type": "admin:error", "message": "Admin access required."})
                    continue
                announcement = _clean_text(data.get("announcement") or "", 180)
                await manager.update_announcement(announcement or None, manager.active_connections.get(websocket), publish_to_chat=True)
            elif event_type == "admin:clear":
                if not _is_chat_admin(manager.active_connections.get(websocket)):
                    await websocket.send_json({"type": "admin:error", "message": "Admin access required."})
                    continue
                await _clear_all_messages()
                await manager.broadcast({"type": "clear"})
            elif event_type and event_type.startswith("call:"):
                receiver_id = data.get("receiver_id")
                caller_id = data.get("caller_id")
                if receiver_id:
                    await manager.send_to_user(receiver_id, data)
                if caller_id and event_type in {"call:accept", "call:decline", "call:hangup"}:
                    await manager.send_to_user(caller_id, data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.broadcast_presence()
        await manager.broadcast_typing()
    except Exception as exc:
        logger.error(f"WebSocket error: {exc}")
        manager.disconnect(websocket)
        await manager.broadcast_presence()
        await manager.broadcast_typing()


@router.get("/config")
async def get_tinode_config():
    return {
        "enabled": os.getenv("TINODE_ENABLED", "false").lower() == "true",
        "server_url": os.getenv("TINODE_SERVER_URL", "localhost:6060"),
        "secure": os.getenv("TINODE_SECURE", "false").lower() == "true",
        "api_key": os.getenv("TINODE_API_KEY", "AQEAAAABAAD_rAp4DJh05a1HAwFT3A6K"),
        "topic": os.getenv("TINODE_TOPIC", "grpCommunityRoom"),
    }


@router.get("/room-state")
async def get_room_state():
    return manager.room_state


@router.get("/player/{fid}")
async def lookup_chat_player(fid: str):
    safe_fid = re.sub(r"\D", "", fid or "")
    if len(safe_fid) != 9:
        raise HTTPException(status_code=400, detail="Enter a valid 9-digit player ID.")

    player = await fetch_player_info(safe_fid)
    if not player:
        raise HTTPException(status_code=404, detail="Player was not found.")

    furnace_level = int(player.get("level") or 0)
    return {
        "id": safe_fid,
        "nickname": player.get("name") or "WOS Player",
        "furnace_level": furnace_level,
        "furnace_level_formatted": format_furnace_level(furnace_level),
        "state_id": str(player.get("id") or ""),
        "avatar_image": player.get("avatar_image") or None,
    }



CHAT_COLLECTION = "global_chat_messages"
PRESENCE_COLLECTION = "global_chat_presence"
REPORTS_COLLECTION = "global_chat_reports"
CHAT_STORE = Path("data/global_chat_messages.json")
PRESENCE_STORE = Path("data/global_chat_presence.json")
REPORTS_STORE = Path("data/global_chat_reports.json")
CHAT_UPLOAD_DIR = Path("data/uploads/chat")
MAX_MESSAGE_LENGTH = 1500
MAX_NAME_LENGTH = 32
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {
    ".aac",
    ".apng",
    ".csv",
    ".gif",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mp3",
    ".ogg",
    ".pdf",
    ".png",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".zip",
}


class ChatAttachment(BaseModel):
    name: str = Field(default="file", max_length=120)
    url: str = Field(..., max_length=500)
    type: Optional[str] = Field(default=None, max_length=120)
    size: Optional[int] = Field(default=None, ge=0, le=MAX_UPLOAD_BYTES)


class ChatMessageCreate(BaseModel):
    content: str = Field(default="", max_length=MAX_MESSAGE_LENGTH)
    display_name: Optional[str] = Field(default=None, max_length=MAX_NAME_LENGTH)
    guest_id: Optional[str] = Field(default=None, max_length=80)
    avatar_url: Optional[str] = Field(default=None, max_length=500)
    timezone: Optional[str] = Field(default=None, max_length=80)
    client_time: Optional[str] = Field(default=None, max_length=80)
    reply_to_id: Optional[str] = Field(default=None, max_length=80)
    target_user_id: Optional[str] = Field(default=None, max_length=80)
    attachments: List[ChatAttachment] = Field(default_factory=list)


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1200)


class ReactionRequest(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=12)
    display_name: Optional[str] = Field(default=None, max_length=MAX_NAME_LENGTH)
    guest_id: Optional[str] = Field(default=None, max_length=80)


class ReportRequest(BaseModel):
    reason: str = Field(default="Needs review", max_length=120)
    details: Optional[str] = Field(default=None, max_length=500)
    display_name: Optional[str] = Field(default=None, max_length=MAX_NAME_LENGTH)
    guest_id: Optional[str] = Field(default=None, max_length=80)
    reported_content: Optional[str] = None
    reported_author_name: Optional[str] = None


class PresenceRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=MAX_NAME_LENGTH)
    guest_id: Optional[str] = Field(default=None, max_length=80)
    avatar_url: Optional[str] = Field(default=None, max_length=500)
    timezone: Optional[str] = Field(default=None, max_length=80)
    furnace_level: Optional[int] = Field(default=None)
    furnace_level_formatted: Optional[str] = Field(default=None, max_length=40)
    state_id: Optional[str] = Field(default=None, max_length=80)


class AnnouncementRequest(BaseModel):
    announcement: str = Field(default="", max_length=180)
    display_name: Optional[str] = Field(default=None, max_length=MAX_NAME_LENGTH)
    guest_id: Optional[str] = Field(default=None, max_length=80)
    avatar_url: Optional[str] = Field(default=None, max_length=500)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: str, max_length: int) -> str:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value or "")
    return cleaned.strip()[:max_length]


def _clean_name(value: Optional[str], fallback: str = "Guest Player") -> str:
    cleaned = _clean_text(value or "", MAX_NAME_LENGTH)
    return cleaned or fallback


def _is_allowed_attachment_url(url: str) -> bool:
    return url.startswith("/api/static/chat/") or url.startswith("https://media.tenor.com/")


def _public_message(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("_id") or doc.get("id") or ""),
        "content": doc.get("content", ""),
        "author": doc.get("author", {}),
        "attachments": doc.get("attachments", []),
        "reply_to": doc.get("reply_to"),
        "target_user_id": doc.get("target_user_id"),
        "reactions": _reaction_summary(doc.get("reactions", [])),
        "report_count": int(doc.get("report_count", 0) or 0),
        "created_at": doc.get("created_at"),
        "timezone": doc.get("timezone"),
        "client_time": doc.get("client_time"),
        "source": doc.get("source", "guest"),
        "announcement_author": doc.get("announcement_author"),
    }


async def _get_collection(name: str = CHAT_COLLECTION):
    if not mongo_enabled() or _get_db_main_async is None:
        return None
    try:
        db = await _get_db_main_async()
        return db[name]
    except Exception as exc:
        logger.warning("Global chat MongoDB unavailable, using JSON fallback: %s", exc)
        return None


def _read_json_store(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to read %s: %s", path, exc)
        return default


def _write_json_store(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_fallback_messages() -> List[Dict[str, Any]]:
    return _read_json_store(CHAT_STORE, [])


def _write_fallback_messages(messages: List[Dict[str, Any]]) -> None:
    _write_json_store(CHAT_STORE, messages[-500:])


def _read_fallback_presence() -> Dict[str, Dict[str, Any]]:
    return _read_json_store(PRESENCE_STORE, {})


def _write_fallback_presence(presence: Dict[str, Dict[str, Any]]) -> None:
    _write_json_store(PRESENCE_STORE, presence)


def _read_fallback_reports() -> List[Dict[str, Any]]:
    return _read_json_store(REPORTS_STORE, [])


def _write_fallback_reports(reports: List[Dict[str, Any]]) -> None:
    _write_json_store(REPORTS_STORE, reports[-1000:])


def _presence_cutoff_iso() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=75)
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _actor_key(author: Dict[str, Any]) -> str:
    return f"{author.get('kind', 'guest')}:{author.get('id') or author.get('name') or 'unknown'}"


def _reaction_summary(reactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summary = []
    for item in reactions or []:
        users = item.get("users", [])
        if not isinstance(users, list):
            users = []
        summary.append({"emoji": item.get("emoji", ""), "count": len(users)})
    return [item for item in summary if item["emoji"] and item["count"] > 0]


async def _find_message(message_id: str) -> Optional[Dict[str, Any]]:
    collection = await _get_collection()
    if collection is not None:
        return await collection.find_one({"_id": str(message_id)})

    for message in _read_fallback_messages():
        if str(message.get("_id") or message.get("id")) == str(message_id):
            return message
    return None


def _reply_snapshot(message: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not message:
        return None
    author = message.get("author") or {}
    content = _clean_text(message.get("content", ""), 160)
    return {
        "id": str(message.get("_id") or message.get("id")),
        "author_name": author.get("name", "Player"),
        "content": content,
    }


async def _resolve_chat_actor(request: Request, display_name: Optional[str], guest_id: Optional[str], avatar_url: Optional[str] = None) -> Dict[str, Any]:
    safe_guest_id = _clean_text(guest_id or "", 80)
    if re.fullmatch(r"\d{9}", safe_guest_id or ""):
        return {
            "id": safe_guest_id,
            "name": _clean_name(display_name, "WOS Player"),
            "username": _clean_name(display_name, "WOS Player"),
            "avatar_url": avatar_url if avatar_url and (avatar_url.startswith("http://") or avatar_url.startswith("https://") or avatar_url.startswith("/api/static/chat/")) else None,
            "kind": "wos",
        }

    auth_header = request.headers.get("Authorization")
    discord_user = await _resolve_discord_user(auth_header)
    if discord_user:
        return discord_user

    safe_guest_id = safe_guest_id or f"guest-{uuid.uuid4().hex[:12]}"
    return {
        "id": safe_guest_id,
        "name": _clean_name(display_name),
        "username": _clean_name(display_name),
        "avatar_url": avatar_url if avatar_url and (avatar_url.startswith("http://") or avatar_url.startswith("https://") or avatar_url.startswith("/api/static/chat/")) else None,
        "kind": "guest",
    }


@router.post("/announcement")
async def post_announcement(payload: AnnouncementRequest, request: Request):
    announcement = _clean_text(payload.announcement or "", 180)
    author = await _resolve_chat_actor(request, payload.display_name, payload.guest_id, payload.avatar_url)

    if not announcement and not _is_chat_admin(author):
        raise HTTPException(status_code=400, detail="Announcement text is required.")

    await manager.update_announcement(announcement or None, author, publish_to_chat=True)
    return {
        "announcement": manager.room_state["announcement"],
        "announcement_author": manager.room_state["announcement_author"],
        "announcement_updated_at": manager.room_state["announcement_updated_at"],
    }


async def _create_announcement_message(announcement: str, author: Dict[str, Any]) -> Dict[str, Any]:
    doc = {
        "_id": uuid.uuid4().hex,
        "content": announcement,
        "author": {
            "id": "announcement",
            "name": "Announcement",
            "username": "announcement",
            "avatar_url": None,
            "kind": "system",
        },
        "attachments": [],
        "reply_to": None,
        "target_user_id": None,
        "reactions": [],
        "report_count": 0,
        "timezone": "",
        "client_time": "",
        "created_at": _utc_now_iso(),
        "source": "announcement",
        "announcement_author": author.get("name"),
        "ip_hint": None,
    }

    collection = await _get_collection()
    if collection is not None:
        await collection.insert_one(doc)
    else:
        messages = _read_fallback_messages()
        messages.append(doc)
        _write_fallback_messages(messages)

    public_msg = _public_message(doc)
    await manager.broadcast_message({"type": "message", "message": public_msg})
    return public_msg


async def _online_count() -> int:
    return len(await _online_users())


async def _online_users() -> List[Dict[str, Any]]:
    cutoff = _presence_cutoff_iso()
    users_by_key: Dict[str, Dict[str, Any]] = {}
    collection = await _get_collection(PRESENCE_COLLECTION)
    if collection is not None:
        cursor = collection.find({"last_seen": {"$gte": cutoff}})
        docs = await cursor.to_list(length=500)
    else:
        docs = [
            item
            for item in _read_fallback_presence().values()
            if item.get("last_seen", "") >= cutoff
        ]

    for doc in docs:
        author = doc.get("author") or {}
        user_id = str(author.get("id") or author.get("name") or "").strip()
        if not user_id:
            continue
        users_by_key[user_id] = {
            "id": author.get("id"),
            "name": author.get("name"),
            "avatar_url": author.get("avatar_url"),
            "kind": author.get("kind"),
            "furnace_level": author.get("furnace_level"),
            "furnace_level_formatted": author.get("furnace_level_formatted"),
            "state_id": author.get("state_id"),
            "last_seen": doc.get("last_seen"),
        }
    return list(users_by_key.values())


def _coerce_int(value: int, fallback: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = fallback
    return min(max(number, min_value), max_value)


def _tenor_image_from_result(item: Dict[str, Any]) -> Optional[Dict[str, str]]:
    formats = item.get("media_formats") or {}
    gif = formats.get("gif") or formats.get("mediumgif") or formats.get("tinygif") or {}
    preview = formats.get("tinygif") or formats.get("nanogif") or gif
    url = gif.get("url")
    preview_url = preview.get("url")
    if not url:
        return None
    return {
        "id": item.get("id"),
        "title": item.get("content_description") or "Tenor GIF",
        "url": url,
        "preview_url": preview_url or url,
    }


async def _translate_with_deepl_sdk(api_key: str, text: str) -> Optional[Dict[str, str]]:
    try:
        import deepl
    except Exception:
        return None

    def run_translation():
        translator = deepl.Translator(api_key)
        result = translator.translate_text(text, target_lang="EN-US")
        return {
            "translated_text": result.text,
            "detected_source_lang": getattr(result, "detected_source_lang", None),
            "provider": "deepl-sdk",
        }

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, run_translation)
    except Exception as exc:
        logger.warning("DeepL SDK translation failed: %s", exc)
        return None


async def _translate_with_deepl_http(api_key: str, text: str) -> Optional[Dict[str, str]]:
    configured_endpoint = os.getenv("DEEPL_API_URL")
    endpoints = [configured_endpoint] if configured_endpoint else [
        "https://api-free.deepl.com/v2/translate",
        "https://api.deepl.com/v2/translate",
    ]
    async with httpx.AsyncClient(timeout=12) as client:
        for endpoint in [item for item in endpoints if item]:
            try:
                response = await client.post(
                    endpoint,
                    data={
                        "auth_key": api_key,
                        "text": text,
                        "target_lang": "EN-US",
                    },
                )
                if response.status_code >= 400:
                    logger.warning("DeepL HTTP translation failed at %s: %s %s", endpoint, response.status_code, response.text[:160])
                    continue
                data = response.json()
                item = (data.get("translations") or [{}])[0]
                return {
                    "translated_text": item.get("text", text),
                    "detected_source_lang": item.get("detected_source_language"),
                    "provider": "deepl-http",
                }
            except Exception as exc:
                logger.warning("DeepL HTTP translation error at %s: %s", endpoint, exc)
    return None


async def _translate_with_free_fallback(text: str) -> Optional[Dict[str, str]]:
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": text, "langpair": "auto|en"},
            )
        if response.status_code >= 400:
            return None
        data = response.json()
        translated = (data.get("responseData") or {}).get("translatedText")
        if not translated:
            return None
        return {
            "translated_text": translated,
            "detected_source_lang": None,
            "provider": "mymemory",
        }
    except Exception as exc:
        logger.warning("Free translation fallback failed: %s", exc)
        return None


async def _update_message_doc(message_id: str, updater):
    collection = await _get_collection()
    if collection is not None:
        doc = await collection.find_one({"_id": str(message_id)})
        if not doc:
            return None
        updated = updater(doc)
        await collection.replace_one({"_id": str(message_id)}, updated)
        return updated

    messages = _read_fallback_messages()
    for index, doc in enumerate(messages):
        if str(doc.get("_id") or doc.get("id")) == str(message_id):
            updated = updater(doc)
            messages[index] = updated
            _write_fallback_messages(messages)
            return updated
    return None


async def _clear_all_messages() -> None:
    collection = await _get_collection()
    if collection is not None:
        await collection.delete_many({})
    else:
        _write_fallback_messages([])


def _build_bot_reply(user_text: str, author: Dict[str, Any], attachments: List[Dict[str, Any]]) -> str:
    text = (user_text or "").strip().lower()
    if "help" in text:
        return "WOS BOT: I can help with chat commands, event planning, rally notes, translations, dice rolls, and uploaded file context."
    if "quote" in text:
        return "WOS BOT: Hold the line, share the intel, and keep the furnace burning."
    if attachments:
        names = ", ".join(item.get("name", "file") for item in attachments[:3])
        return f"WOS BOT: I received {names}. Tell me what you want extracted or summarized from it."
    if text:
        return f"WOS BOT: {author.get('name', 'Chief')}, noted. For Whiteout planning, turn that into a clear action, owner, time, and reminder."
    return "WOS BOT: Send a message or file and I will help organize it."


async def _resolve_discord_user(auth_header: Optional[str]) -> Optional[Dict[str, Any]]:
    if not auth_header:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": auth_header},
            )
        if response.status_code != 200:
            return None
        user = response.json()
        avatar_hash = user.get("avatar")
        avatar_url = None
        if avatar_hash:
            avatar_url = f"https://cdn.discordapp.com/avatars/{user.get('id')}/{avatar_hash}.png?size=96"
        return {
            "id": str(user.get("id")),
            "name": _clean_name(user.get("global_name") or user.get("username"), "Discord Player"),
            "username": _clean_name(user.get("username"), "discord"),
            "avatar_url": avatar_url,
            "kind": "discord",
        }
    except Exception as exc:
        logger.warning("Failed to resolve Discord user for chat: %s", exc)
        return None


@router.get("/messages")
async def list_messages(request: Request, limit: int = 80, guest_id: Optional[str] = None):
    safe_limit = min(max(int(limit or 80), 1), 100)
    
    # Resolve the caller to filter private messages
    author = await _resolve_chat_actor(request, None, guest_id)
    caller_id = author.get("id")

    def _is_visible(doc: Dict[str, Any]) -> bool:
        target_id = doc.get("target_user_id")
        if not target_id:
            return True
        msg_author = doc.get("author", {})
        author_id = msg_author.get("id") or msg_author.get("name")
        return caller_id in (target_id, author_id)

    collection = await _get_collection()
    if collection is not None:
        # Build query for public or private intended for me or by me
        query = {
            "$or": [
                {"target_user_id": {"$in": [None, ""]}},
                {"target_user_id": caller_id},
                {"author.id": caller_id}
            ]
        }
        cursor = collection.find(query).sort("created_at", -1).limit(safe_limit)
        docs = await cursor.to_list(length=safe_limit)
        docs.reverse()
        return {"messages": [_public_message(doc) for doc in docs], "online_count": await _online_count()}

    messages = [doc for doc in _read_fallback_messages() if _is_visible(doc)][-safe_limit:]
    return {"messages": [_public_message(doc) for doc in messages], "online_count": await _online_count()}


@router.post("/messages", status_code=201)
async def create_message(payload: ChatMessageCreate, request: Request):
    content = _clean_text(payload.content, MAX_MESSAGE_LENGTH)
    attachments = [
        item.dict()
        for item in payload.attachments[:4]
        if _is_allowed_attachment_url(item.url)
    ]
    if not content and not attachments:
        raise HTTPException(status_code=400, detail="Message text or a file is required.")

    author = await _resolve_chat_actor(request, payload.display_name, payload.guest_id, payload.avatar_url)
    source = author.get("kind", "guest")
    reply_to = _reply_snapshot(await _find_message(payload.reply_to_id)) if payload.reply_to_id else None

    doc = {
        "_id": uuid.uuid4().hex,
        "content": content,
        "author": author,
        "attachments": attachments,
        "reply_to": reply_to,
        "target_user_id": payload.target_user_id,
        "reactions": [],
        "report_count": 0,
        "timezone": _clean_text(payload.timezone or "", 80),
        "client_time": _clean_text(payload.client_time or "", 80),
        "created_at": _utc_now_iso(),
        "source": source,
        "ip_hint": request.client.host if request.client else None,
    }

    collection = await _get_collection()
    if collection is not None:
        await collection.insert_one(doc)
        excess_count = await collection.count_documents({})
        if excess_count > 500:
            old_cursor = collection.find({}, {"_id": 1}).sort("created_at", 1).limit(excess_count - 500)
            old_docs = await old_cursor.to_list(length=excess_count - 500)
            old_ids = [item["_id"] for item in old_docs]
            if old_ids:
                await collection.delete_many({"_id": {"$in": old_ids}})
    else:
        messages = _read_fallback_messages()
        messages.append(doc)
        _write_fallback_messages(messages)
        
    public_msg = _public_message(doc)
    await manager.broadcast_message({"type": "message", "message": public_msg})
    if str(payload.target_user_id or "") == "wos_bot":
        bot_doc = {
            "_id": uuid.uuid4().hex,
            "content": _build_bot_reply(content, author, attachments),
            "author": {
                "id": "wos_bot",
                "name": "WOS BOT",
                "username": "wos_bot",
                "avatar_url": None,
                "kind": "bot",
            },
            "attachments": [],
            "reply_to": _reply_snapshot(doc),
            "target_user_id": author.get("id"),
            "reactions": [],
            "report_count": 0,
            "timezone": "",
            "client_time": "",
            "created_at": _utc_now_iso(),
            "source": "bot",
        }
        collection = await _get_collection()
        if collection is not None:
            await collection.insert_one(bot_doc)
        else:
            messages = _read_fallback_messages()
            messages.append(bot_doc)
            _write_fallback_messages(messages)
        await manager.broadcast_message({"type": "message", "message": _public_message(bot_doc)})
    return {"message": public_msg}

@router.delete("/messages/{message_id}")
async def delete_message(message_id: str, request: Request, guest_id: Optional[str] = None):
    author = await _resolve_chat_actor(request, None, guest_id)
    caller_id = author.get("id")
    
    collection = await _get_collection()
    if collection is not None:
        doc = await collection.find_one({"_id": str(message_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Message not found.")
        
        msg_author = doc.get("author", {})
        if msg_author.get("id") != caller_id:
            raise HTTPException(status_code=403, detail="You can only delete your own messages.")
            
        await collection.delete_one({"_id": str(message_id)})
    else:
        messages = _read_fallback_messages()
        target_idx = next((i for i, m in enumerate(messages) if str(m.get("_id") or m.get("id")) == message_id), None)
        if target_idx is None:
            raise HTTPException(status_code=404, detail="Message not found.")
            
        msg_author = messages[target_idx].get("author", {})
        if msg_author.get("id") != caller_id:
            raise HTTPException(status_code=403, detail="You can only delete your own messages.")
            
        messages.pop(target_idx)
        _write_fallback_messages(messages)

    await manager.broadcast({"type": "delete", "message_id": message_id})
    return {"status": "deleted"}


@router.post("/presence")
async def update_presence(payload: PresenceRequest, request: Request):
    author = await _resolve_chat_actor(request, payload.display_name, payload.guest_id, payload.avatar_url)
    if author.get("kind") == "wos":
        author["furnace_level"] = payload.furnace_level
        author["furnace_level_formatted"] = payload.furnace_level_formatted
        author["state_id"] = payload.state_id
    key = _actor_key(author)
    now = _utc_now_iso()
    doc = {
        "_id": key,
        "author": author,
        "timezone": _clean_text(payload.timezone or "", 80),
        "last_seen": now,
        "updated_at": now,
    }

    collection = await _get_collection(PRESENCE_COLLECTION)
    if collection is not None:
        await collection.update_one({"_id": key}, {"$set": doc}, upsert=True)
    else:
        presence = _read_fallback_presence()
        cutoff = _presence_cutoff_iso()
        presence = {item_key: item for item_key, item in presence.items() if item.get("last_seen", "") >= cutoff}
        presence[key] = doc
        _write_fallback_presence(presence)

    users = await _online_users()
    return {"online_count": len(users), "users": users, "you": author}


@router.get("/presence")
async def get_presence():
    users = await _online_users()
    return {"online_count": len(users), "users": users}


@router.post("/messages/{message_id}/react")
async def react_to_message(message_id: str, payload: ReactionRequest, request: Request):
    emoji = _clean_text(payload.emoji, 12)
    if not emoji:
        raise HTTPException(status_code=400, detail="Emoji is required.")

    author = await _resolve_chat_actor(request, payload.display_name, payload.guest_id)
    actor = _actor_key(author)

    def updater(doc: Dict[str, Any]) -> Dict[str, Any]:
        reactions = doc.get("reactions") or []
        match = None
        for item in reactions:
            if item.get("emoji") == emoji:
                match = item
                break
        if match is None:
            match = {"emoji": emoji, "users": []}
            reactions.append(match)
        users = match.get("users")
        if not isinstance(users, list):
            users = []
        if actor in users:
            users = [item for item in users if item != actor]
        else:
            users.append(actor)
        match["users"] = users
        doc["reactions"] = [item for item in reactions if item.get("users")]
        doc["updated_at"] = _utc_now_iso()
        return doc

    updated = await _update_message_doc(message_id, updater)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found.")
    reactions_sum = _reaction_summary(updated.get("reactions", []))
    await manager.broadcast({
        "type": "reaction",
        "message_id": str(message_id),
        "reactions": reactions_sum
    })
    return {"message": _public_message(updated), "reactions": reactions_sum}


@router.post("/messages/{message_id}/report")
async def report_message(message_id: str, payload: ReportRequest, request: Request):
    message = await _find_message(message_id)

    author = await _resolve_chat_actor(request, payload.display_name, payload.guest_id)
    now = _utc_now_iso()
    
    reported_content = payload.reported_content or (message.get("content") if message else "Unknown content (Tinode)")
    reported_author_name = payload.reported_author_name or (message.get("author", {}).get("name") if message else "Unknown author")

    report = {
        "_id": uuid.uuid4().hex,
        "message_id": str(message_id),
        "reporter": author,
        "reason": _clean_text(payload.reason, 120),
        "details": _clean_text(payload.details or "", 500),
        "created_at": now,
        "reported_content": reported_content,
        "reported_author_name": reported_author_name,
    }

    reports_collection = await _get_collection(REPORTS_COLLECTION)
    if reports_collection is not None:
        await reports_collection.insert_one(report)
    else:
        reports = _read_fallback_reports()
        reports.append(report)
        _write_fallback_reports(reports)

    if message:
        def updater(doc: Dict[str, Any]) -> Dict[str, Any]:
            doc["report_count"] = int(doc.get("report_count", 0) or 0) + 1
            doc["updated_at"] = now
            return doc

        await _update_message_doc(message_id, updater)
        
    return {"status": "ok", "message": "Report sent for review."}


@router.get("/tenor")
async def search_tenor(q: str = "whiteout survival", limit: int = 12):
    api_key = os.getenv("TENOR_API_KEY") or os.getenv("GOOGLE_TENOR_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Tenor GIF search is not configured.")

    safe_limit = _coerce_int(limit, 12, 1, 24)
    query = _clean_text(q or "whiteout survival", 80) or "whiteout survival"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://tenor.googleapis.com/v2/search",
                params={
                    "key": api_key,
                    "client_key": "wos-global-chat",
                    "q": query,
                    "limit": safe_limit,
                    "media_filter": "gif,tinygif,nanogif",
                    "contentfilter": "medium",
                },
            )
        if response.status_code >= 400:
            logger.warning("Tenor search failed: %s %s", response.status_code, response.text[:160])
            raise HTTPException(status_code=502, detail="Tenor search failed.")
        data = response.json()
        gifs = [_tenor_image_from_result(item) for item in data.get("results", [])]
        return {"results": [item for item in gifs if item]}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Tenor search error: %s", exc)
        raise HTTPException(status_code=502, detail="GIF search failed.")

@router.get("/giphy")
async def search_giphy(q: str = "whiteout survival", limit: int = 12):
    api_key = os.getenv("GIPHY_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Giphy search is not configured.")

    safe_limit = _coerce_int(limit, 12, 1, 24)
    query = _clean_text(q or "whiteout survival", 80) or "whiteout survival"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api.giphy.com/v1/gifs/search",
                params={
                    "api_key": api_key,
                    "q": query,
                    "limit": safe_limit,
                    "rating": "pg-13",
                },
            )
        if response.status_code >= 400:
            logger.warning("Giphy search failed: %s %s", response.status_code, response.text[:160])
            raise HTTPException(status_code=502, detail="Giphy search failed.")
        data = response.json()
        
        results = []
        for item in data.get("data", []):
            images = item.get("images", {})
            original = images.get("original", {})
            fixed_width = images.get("fixed_width", {})
            
            if original.get("url"):
                results.append({
                    "id": item.get("id"),
                    "title": item.get("title") or "Giphy GIF",
                    "url": original.get("url"),
                    "preview_url": fixed_width.get("url") or original.get("url")
                })
                
        return {"results": results}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Giphy search error: %s", exc)
        raise HTTPException(status_code=502, detail="GIF search failed.")


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 8 MB.")

    original = Path(file.filename).name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="This file type is not allowed.")

    CHAT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    path = CHAT_UPLOAD_DIR / stored_name
    path.write_bytes(content)

    return {
        "attachment": {
            "name": original[:120],
            "url": f"/api/static/chat/{stored_name}",
            "type": file.content_type,
            "size": len(content),
        }
    }


@router.post("/translate")
async def translate_to_english(payload: TranslateRequest):
    api_key = os.getenv("DEEPL_API_KEY")
    if api_key:
        deepl_sdk = await _translate_with_deepl_sdk(api_key, payload.text)
        if deepl_sdk:
            return deepl_sdk
        deepl_http = await _translate_with_deepl_http(api_key, payload.text)
        if deepl_http:
            return deepl_http

    fallback = await _translate_with_free_fallback(payload.text)
    if fallback:
        return fallback

    raise HTTPException(status_code=502, detail="Translation failed.")
