import logging
import re
from typing import Any, Dict, List, Optional

import discord
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from db.mongo_adapters import (
        AllianceMembersAdapter,
        PersistentViewsAdapter,
        RecordsAdapter,
        ServerAllianceAdapter,
        mongo_enabled,
    )
except Exception:
    mongo_enabled = lambda: False
    AllianceMembersAdapter = None
    PersistentViewsAdapter = None
    RecordsAdapter = None
    ServerAllianceAdapter = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/records", tags=["Records"])

FID_RE = re.compile(r"^\d{9}$")
FIELD_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,40}$")


class RecordCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    created_by: Optional[int] = 0


class RecordRename(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=50)


class RecordMemberAdd(BaseModel):
    fids: List[str] = Field(default_factory=list)
    fetch_live: bool = True
    added_by: Optional[int] = 0


class RecordMemberRemove(BaseModel):
    fids: List[str] = Field(default_factory=list)


class RecordColumnPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)


class RecordMemberFieldUpdate(BaseModel):
    field: str = Field(..., min_length=1, max_length=40)
    value: Any = ""


class RecordPublishPayload(BaseModel):
    channel_id: int
    view_type: str = Field("list", pattern="^(list|detail)$")
    record_name: Optional[str] = Field(None, max_length=50)


def _require_records():
    if not mongo_enabled() or RecordsAdapter is None:
        raise HTTPException(status_code=503, detail="MongoDB records are not available.")


def _clean_record_name(name: str) -> str:
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        raise HTTPException(status_code=400, detail="Record name is required.")
    if len(cleaned) > 50:
        raise HTTPException(status_code=400, detail="Record name must be 50 characters or less.")
    if ":" in cleaned:
        raise HTTPException(status_code=400, detail="Record name cannot contain ':'.")
    return cleaned


def _clean_fids(fids: List[str]) -> List[str]:
    cleaned = []
    seen = set()
    for raw in fids or []:
        fid = str(raw).strip()
        if not FID_RE.match(fid):
            continue
        if fid not in seen:
            seen.add(fid)
            cleaned.append(fid)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Provide at least one valid 9-digit FID.")
    return cleaned[:250]


def _serialize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    members = record.get("members", []) if record else []
    furnace_levels = []
    for member in members:
        try:
            furnace_levels.append(int(member.get("furnace_lv", 0) or 0))
        except Exception:
            pass
    return {
        "name": record.get("record_name"),
        "record_name": record.get("record_name"),
        "created_by": record.get("created_by"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "custom_columns": record.get("custom_columns", []),
        "member_count": len(members),
        "highest_furnace": max(furnace_levels) if furnace_levels else 0,
        "average_furnace": round(sum(furnace_levels) / len(furnace_levels), 1) if furnace_levels else 0,
        "members": members,
    }


async def _fetch_player(fid: str) -> Optional[Dict[str, Any]]:
    try:
        from cogs.login_handler import LoginHandler

        result = await LoginHandler().fetch_player_data(fid)
        if result.get("status") == "success" and result.get("data"):
            data = result["data"]
            return {
                "nickname": data.get("nickname", "Unknown"),
                "furnace_lv": int(data.get("stove_lv", data.get("furnace_lv", 0)) or 0),
                "avatar_image": data.get("avatar_image", ""),
            }
    except Exception as exc:
        logger.warning("Failed to fetch player data for %s: %s", fid, exc)
    return None


@router.get("/{guild_id}")
async def list_records(guild_id: int):
    _require_records()
    records = RecordsAdapter.get_all_records(int(guild_id))
    records.sort(key=lambda item: (item.get("record_name") or "").lower())
    return {"records": records}


@router.post("/{guild_id}")
async def create_record(guild_id: int, payload: RecordCreate):
    _require_records()
    name = _clean_record_name(payload.name)
    if not RecordsAdapter.create_record(int(guild_id), name, int(payload.created_by or 0)):
        raise HTTPException(status_code=409, detail=f"Record '{name}' already exists.")
    return {"status": "success", "record_name": name}


@router.get("/{guild_id}/{record_name}")
async def get_record(guild_id: int, record_name: str):
    _require_records()
    name = _clean_record_name(record_name)
    record = RecordsAdapter.get_record(int(guild_id), name)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found.")
    return _serialize_record(record)


@router.patch("/{guild_id}/{record_name}")
async def rename_record(guild_id: int, record_name: str, payload: RecordRename):
    _require_records()
    old_name = _clean_record_name(record_name)
    new_name = _clean_record_name(payload.new_name)
    if old_name == new_name:
        return {"status": "success", "record_name": new_name}
    if not RecordsAdapter.rename_record(int(guild_id), old_name, new_name):
        raise HTTPException(status_code=409, detail=f"Could not rename to '{new_name}'. It may already exist.")
    return {"status": "success", "record_name": new_name}


@router.delete("/{guild_id}/{record_name}")
async def delete_record(guild_id: int, record_name: str):
    _require_records()
    name = _clean_record_name(record_name)
    if not RecordsAdapter.delete_record(int(guild_id), name):
        raise HTTPException(status_code=404, detail="Record not found.")
    return {"status": "success"}


@router.post("/{guild_id}/{record_name}/members")
async def add_record_members(guild_id: int, record_name: str, payload: RecordMemberAdd):
    _require_records()
    name = _clean_record_name(record_name)
    if not RecordsAdapter.get_record(int(guild_id), name):
        raise HTTPException(status_code=404, detail="Record not found.")
    fids = _clean_fids(payload.fids)
    results = []
    added = 0
    failed = 0
    for fid in fids:
        player = await _fetch_player(fid) if payload.fetch_live else None
        member_data = player or {"nickname": "Unknown", "furnace_lv": 0, "avatar_image": ""}
        member_data["added_by"] = int(payload.added_by or 0)
        ok = RecordsAdapter.add_member_to_record(int(guild_id), name, fid, member_data)
        if ok:
            added += 1
        else:
            failed += 1
        results.append({"fid": fid, "status": "added" if ok else "failed", **member_data})
    return {"status": "success", "added": added, "failed": failed, "results": results}


@router.post("/{guild_id}/{record_name}/members/from-alliance")
async def add_alliance_members(guild_id: int, record_name: str):
    _require_records()
    if AllianceMembersAdapter is None or ServerAllianceAdapter is None:
        raise HTTPException(status_code=503, detail="Alliance member data is not available.")
    name = _clean_record_name(record_name)
    alliance_id = ServerAllianceAdapter.get_alliance(int(guild_id))
    if not alliance_id:
        raise HTTPException(status_code=400, detail="No alliance is assigned to this server.")
    members = AllianceMembersAdapter.get_all_members()
    selected = [m for m in members if int(m.get("alliance", 0) or m.get("alliance_id", 0) or 0) == int(alliance_id)]
    added = 0
    for member in selected:
        fid = str(member.get("fid") or "").strip()
        if not FID_RE.match(fid):
            continue
        if RecordsAdapter.add_member_to_record(
            int(guild_id),
            name,
            fid,
            {
                "nickname": member.get("nickname", "Unknown"),
                "furnace_lv": int(member.get("furnace_lv", 0) or 0),
                "avatar_image": member.get("avatar_image", ""),
                "added_by": 0,
            },
        ):
            added += 1
    return {"status": "success", "added": added, "available": len(selected)}


@router.delete("/{guild_id}/{record_name}/members")
async def remove_record_members(guild_id: int, record_name: str, payload: RecordMemberRemove):
    _require_records()
    name = _clean_record_name(record_name)
    fids = _clean_fids(payload.fids)
    removed = 0
    failed = 0
    for fid in fids:
        if RecordsAdapter.remove_member_from_record(int(guild_id), name, fid):
            removed += 1
        else:
            failed += 1
    return {"status": "success", "removed": removed, "failed": failed}


@router.post("/{guild_id}/{record_name}/columns")
async def add_column(guild_id: int, record_name: str, payload: RecordColumnPayload):
    _require_records()
    name = _clean_record_name(record_name)
    column = payload.name.strip()
    if not FIELD_RE.match(column):
        raise HTTPException(status_code=400, detail="Column names may contain letters, numbers, spaces, dots, dashes, and underscores.")
    if not RecordsAdapter.add_custom_column(int(guild_id), name, column):
        raise HTTPException(status_code=404, detail="Record not found.")
    return {"status": "success", "column": column}


@router.delete("/{guild_id}/{record_name}/columns/{column_name}")
async def remove_column(guild_id: int, record_name: str, column_name: str):
    _require_records()
    name = _clean_record_name(record_name)
    column = column_name.strip()
    if not RecordsAdapter.remove_custom_column(int(guild_id), name, column):
        raise HTTPException(status_code=404, detail="Column not found.")
    return {"status": "success"}


@router.patch("/{guild_id}/{record_name}/members/{fid}")
async def update_member_field(guild_id: int, record_name: str, fid: str, payload: RecordMemberFieldUpdate):
    _require_records()
    name = _clean_record_name(record_name)
    clean_fid = _clean_fids([fid])[0]
    field = payload.field.strip()
    if field in {"fid", "added_at", "added_by"}:
        raise HTTPException(status_code=400, detail="This field cannot be edited.")
    if not FIELD_RE.match(field):
        raise HTTPException(status_code=400, detail="Invalid field name.")
    value = payload.value
    if isinstance(value, str):
        value = value[:250]
    if field == "furnace_lv":
        try:
            value = int(value or 0)
        except Exception:
            raise HTTPException(status_code=400, detail="Furnace level must be a number.")
    if not RecordsAdapter.update_member_field(int(guild_id), name, clean_fid, field, value):
        raise HTTPException(status_code=404, detail="Member or record not found.")
    return {"status": "success"}


@router.post("/{guild_id}/publish")
async def publish_record_view(guild_id: int, payload: RecordPublishPayload, request: Request):
    _require_records()
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        raise HTTPException(status_code=503, detail="Discord bot is not available.")
    guild = bot.get_guild(int(guild_id))
    if not guild:
        raise HTTPException(status_code=404, detail="Guild not found.")
    channel = guild.get_channel(int(payload.channel_id))
    if not channel or not hasattr(channel, "send"):
        raise HTTPException(status_code=404, detail="Channel not found or is not a text channel.")

    try:
        if payload.view_type == "detail":
            record_name = _clean_record_name(payload.record_name or "")
            if not RecordsAdapter.get_record(int(guild_id), record_name):
                raise HTTPException(status_code=404, detail="Record not found.")
            from cogs.bot_operations import PersistentRecordDetailView

            view = PersistentRecordDetailView(record_name=record_name)
            embed = await view.create_embed(guild.id, guild.name)
            view_type = "recorddetail"
            metadata = {"record_name": record_name}
        else:
            from cogs.bot_operations import PersistentRecordsView

            view = PersistentRecordsView()
            embed, _ = await view.create_embed(guild.id, guild.name)
            view_type = "recordlist"
            metadata = {}

        message = await channel.send(embed=embed, view=view)
        bot.add_view(view, message_id=message.id)
        persisted = False
        if PersistentViewsAdapter is not None and mongo_enabled():
            persisted = PersistentViewsAdapter.register_view(
                guild_id=guild.id,
                channel_id=channel.id,
                message_id=message.id,
                view_type=view_type,
                metadata=metadata,
            )
        return {
            "status": "success",
            "message_id": str(message.id),
            "channel_id": str(channel.id),
            "persisted": persisted,
            "jump_url": getattr(message, "jump_url", None),
        }
    except HTTPException:
        raise
    except discord.Forbidden:
        raise HTTPException(status_code=403, detail="Bot cannot send embeds in that channel.")
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=f"Discord rejected the record view: {exc}")
    except Exception as exc:
        logger.error("Failed to publish record view for guild %s: %s", guild_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to publish record view.")
