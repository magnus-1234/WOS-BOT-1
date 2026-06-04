from datetime import datetime
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/message-templates", tags=["Message Templates"])

VALID_CATEGORIES = {
    "state-transfer-chat",
    "unicodes",
    "emojis",
    "funny",
    "alliance-recruit",
    "various",
    "leaders",
    "nsfw",
}


def _copy_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _collection():
    mongo_uri = os.environ.get("MONGO_URI")
    if not mongo_uri:
        return None
    mongo_db = (
        os.environ.get("MONGO_DB_NAME")
        or os.environ.get("MONGO_DB_WOS")
        or os.environ.get("MONGO_DB")
        or "wosbot"
    )
    from pymongo import MongoClient

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
    return client[mongo_db]["message_templates"]


def _public_template(doc: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    clean = {key: value for key, value in doc.items() if key != "_id"}
    clean["text"] = _copy_text(clean.get("text"))
    clean["rawText"] = _copy_text(clean.get("rawText") or clean.get("text"))
    clean["canManage"] = bool(user_id and clean.get("creatorUserId") == user_id)
    return clean


def _categories(values: List[str], fallback: str) -> List[str]:
    selected = [value for value in values if value in VALID_CATEGORIES]
    if fallback in VALID_CATEGORIES:
        selected.insert(0, fallback)
    unique = []
    for value in selected:
        if value not in unique:
            unique.append(value)
    return unique or ["state-transfer-chat"]


async def _payload(request: Request) -> Dict[str, Any]:
    form = await request.form()
    category = str(form.get("category") or "state-transfer-chat")
    categories = _categories([str(value) for value in form.getlist("categories")], category)
    text = _copy_text(form.get("text"))
    now = datetime.utcnow().isoformat()
    tags = [
        tag.strip().lstrip("#")
        for tag in str(form.get("tags") or "").replace(",", " ").split()
        if tag.strip().lstrip("#")
    ][:12]
    return {
        "title": str(form.get("title") or "Untitled template").strip()[:90] or "Untitled template",
        "category": categories[0],
        "categories": categories,
        "description": str(form.get("description") or "").strip()[:360],
        "text": text,
        "rawText": text,
        "imageUrl": str(form.get("imageUrl") or "").strip(),
        "tags": tags,
        "creatorName": str(form.get("creatorName") or "Community").strip()[:80] or "Community",
        "creatorUserId": str(form.get("creatorUserId") or "").strip() or None,
        "updatedAt": now,
    }


@router.get("")
async def list_templates(request: Request, sort: str = "popular", category: Optional[str] = None, tag: Optional[str] = None, limit: int = 80):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Template storage is not available")
    query: Dict[str, Any] = {}
    if category and category != "all":
        query["$or"] = [{"category": category}, {"categories": category}]
    if tag:
        query["tags"] = {"$regex": f"^{tag}$", "$options": "i"}
    sort_spec = [("createdAt", -1)] if sort == "recent" else [("likes", -1), ("shares", -1), ("createdAt", -1)]
    docs = list(col.find(query).sort(sort_spec).limit(max(1, min(limit, 100))))
    user_id = request.headers.get("x-user-id")
    return {"templates": [_public_template(doc, user_id) for doc in docs], "favoriteIds": []}


@router.get("/me/uploads")
async def my_uploads(request: Request, limit: int = 80):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Template storage is not available")
    user_id = request.headers.get("x-user-id")
    query = {"creatorUserId": user_id} if user_id else {}
    docs = list(col.find(query).sort("createdAt", -1).limit(max(1, min(limit, 100))))
    return {"templates": [_public_template(doc, user_id) for doc in docs], "favoriteIds": []}


@router.get("/me/favorites")
async def my_favorites(request: Request, limit: int = 80):
    return {"templates": [], "favoriteIds": []}


@router.post("")
async def create_template(request: Request):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Template storage is not available")
    payload = await _payload(request)
    now = payload["updatedAt"]
    template = {
        **payload,
        "id": str(uuid.uuid4()),
        "likes": 0,
        "shares": 0,
        "createdAt": now,
        "builtin": False,
    }
    col.insert_one(template.copy())
    return {"template": _public_template(template, payload.get("creatorUserId"))}


@router.patch("/{template_id}")
async def update_template(request: Request, template_id: str):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Template storage is not available")
    existing = col.find_one({"id": template_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")
    payload = await _payload(request)
    user_id = payload.get("creatorUserId")
    if existing.get("creatorUserId") and user_id and existing.get("creatorUserId") != user_id:
        raise HTTPException(status_code=403, detail="You can only edit your own templates")
    col.update_one({"id": template_id}, {"$set": payload})
    updated = col.find_one({"id": template_id})
    return {"template": _public_template(updated, user_id)}


@router.delete("/{template_id}")
async def delete_template(request: Request, template_id: str):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Template storage is not available")
    existing = col.find_one({"id": template_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")
    user_id = request.headers.get("x-user-id")
    if existing.get("creatorUserId") and user_id and existing.get("creatorUserId") != user_id:
        raise HTTPException(status_code=403, detail="You can only delete your own templates")
    col.delete_one({"id": template_id})
    return {"status": "success"}


@router.post("/{template_id}/like")
async def like_template(template_id: str):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Template storage is not available")
    col.update_one({"id": template_id}, {"$inc": {"likes": 1}})
    template = col.find_one({"id": template_id})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": _public_template(template)}


@router.delete("/{template_id}/like")
async def unlike_template(template_id: str):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Template storage is not available")
    col.update_one({"id": template_id, "likes": {"$gt": 0}}, {"$inc": {"likes": -1}})
    template = col.find_one({"id": template_id})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": _public_template(template)}


@router.post("/{template_id}/share")
async def share_template(template_id: str):
    col = _collection()
    if col is None:
        raise HTTPException(status_code=503, detail="Template storage is not available")
    col.update_one({"id": template_id}, {"$inc": {"shares": 1}})
    template = col.find_one({"id": template_id})
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": _public_template(template)}
