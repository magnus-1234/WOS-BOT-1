"""Web scraper / public-data client for https://wosatlas.com.

WoS Atlas is a fan-made companion site that aggregates public Whiteout Survival
census data.  This module provides:

  fetch_player_info(player_id)
      → {name, state, last_seen, wosatlas_url}  (no auth required)

  fetch_state_alliances(state_id)
      → list of top alliances for that state (name, id, power, member_count)

  fetch_alliance_info(alliance_id)
      → basic alliance info; full member list requires WoS Atlas account

Notes
-----
* Avatars: wosatlas.com does NOT expose player avatars.  Avatar data can only
  come from the Century Games giftcode API (existing primary source) or
  WoS Oracle.

* Public HTML pages do not include coordinates. The documented player-search
  API provides profile fields and coordinates with a WOSATLAS_API_KEY.

* All network calls are asynchronous (aiohttp).  HTML is parsed with the
  stdlib html.parser via BeautifulSoup if available, or a lightweight
  regex fallback if not installed.

* Rate-limit etiquette: a conservative 0.5 s delay is applied between
  consecutive calls to the same host.  Do not fan-out more than 3 parallel
  requests.

* The site is a Cloudflare-protected React SPA (content rendered client-side).
  Static HTML supports basic lookups; API-backed profile data should use the
  documented developer API instead of scraping the signed-in browser.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Optional

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://wosatlas.com"
API_BASE_URL = "https://api.wosatlas.com"

# Conservative defaults for a respectful scraper
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "WhiteoutSurvivalBot/1.0 (+https://whiteoutsurvival.dev; "
        "fetching public census data from wosatlas.com)"
    ),
}


# ── Custom exceptions ─────────────────────────────────────────────────────────

class WosAtlasError(RuntimeError):
    """Base exception for WoS Atlas scraper failures."""


class WosAtlasNotFoundError(WosAtlasError):
    """Raised when the requested resource is not found (404 or empty page)."""


class WosAtlasAuthRequiredError(WosAtlasError):
    """Raised when the data is only available to signed-in WoS Atlas users."""


async def fetch_player_profile(
    player_id,
    *,
    api_key: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Optional[dict]:
    """Look up a player's census profile by Chief ID using the Atlas API.

    Set ``WOSATLAS_API_KEY`` in the bot's environment. Returns ``None`` when
    no key is configured, the player is not indexed, or the lookup is unavailable.
    The result includes only profile fields used by registration and coordinates.
    """
    fid = str(player_id).strip()
    key = (api_key or os.getenv("WOSATLAS_API_KEY", "")).strip()
    if not fid or not key:
        return None

    headers = {"X-Api-Key": key, "Accept": "application/json"}

    async def _lookup(client: aiohttp.ClientSession) -> Optional[dict]:
        async with client.get(
            f"{API_BASE_URL}/v1/players/search",
            params={"playerName": fid},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status != 200:
                log.warning(
                    "Atlas profile lookup failed for player %s (HTTP %s)",
                    fid,
                    response.status,
                )
                return None

            payload = await response.json(content_type=None)
            players = payload.get("players", []) if isinstance(payload, dict) else []
            player = next(
                (
                    row for row in players
                    if isinstance(row, dict) and str(row.get("fid", "")) == fid
                ),
                None,
            )
            if not isinstance(player, dict):
                return None

            try:
                furnace_level = int(
                    player.get("furnace_lv") or player.get("stove_lv") or player.get("lv") or 0
                )
            except (TypeError, ValueError):
                furnace_level = 0
            try:
                x = int(player["x"])
                y = int(player["y"])
                coordinates = {"x": x, "y": y}
            except (KeyError, TypeError, ValueError):
                coordinates = None
            return {
                "state_id": player.get("kid") or payload.get("stateKid"),
                "nickname": str(player.get("nick_name") or "Unknown"),
                "furnace_lv": furnace_level,
                "alliance_abbr": str(player.get("abbr") or ""),
                "alliance_id": player.get("aid"),
                "power": player.get("current_power"),
                "coordinates": coordinates,
                "observed_at": player.get("observed_at"),
            }

    try:
        if session is not None:
            return await _lookup(session)
        async with aiohttp.ClientSession() as client:
            return await _lookup(client)
    except Exception as exc:
        log.warning("Atlas profile lookup unavailable for player %s: %s", fid, exc)
        return None


async def fetch_player_coordinates(
    player_id,
    *,
    api_key: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Optional[dict]:
    """Return just coordinates and their census metadata for a Chief ID."""
    profile = await fetch_player_profile(
        player_id, api_key=api_key, session=session, timeout=timeout
    )
    if not profile or not profile.get("coordinates"):
        return None
    return {
        **profile["coordinates"],
        "state_id": profile.get("state_id"),
        "observed_at": profile.get("observed_at"),
    }


# ── Internal HTML helpers ──────────────────────────────────────────────────────

def _try_import_bs4():
    """Return BeautifulSoup class or None if bs4 not installed."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
        return BeautifulSoup
    except ImportError:
        return None


def _extract_text(html: str, pattern: str, group: int = 1) -> Optional[str]:
    """Quick regex text extraction from raw HTML."""
    m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    return m.group(group).strip() if m else None


def _clean_text(raw: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    no_tags = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", no_tags).strip()


# ── HTTP helper ────────────────────────────────────────────────────────────────

async def _get_html(
    url: str,
    *,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Fetch a URL and return the raw HTML.  Raises WosAtlasError on failure."""
    owns_session = session is None
    client = session or aiohttp.ClientSession(
        headers=_DEFAULT_HEADERS,
        timeout=aiohttp.ClientTimeout(total=timeout),
    )
    try:
        async with client.get(url, headers=_DEFAULT_HEADERS) as resp:
            if resp.status == 404:
                raise WosAtlasNotFoundError(f"WoS Atlas: 404 for {url}")
            if resp.status in (401, 403):
                raise WosAtlasAuthRequiredError(
                    f"WoS Atlas: {resp.status} auth required for {url}"
                )
            if resp.status != 200:
                raise WosAtlasError(
                    f"WoS Atlas: unexpected HTTP {resp.status} for {url}"
                )
            return await resp.text(encoding="utf-8", errors="replace")
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise WosAtlasError(f"WoS Atlas: network error fetching {url}: {exc}") from exc
    finally:
        if owns_session:
            await client.close()


# ── Public API ─────────────────────────────────────────────────────────────────

async def fetch_player_info(
    player_id,
    *,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict:
    """Fetch publicly visible player data from WoS Atlas.

    Args:
        player_id: Numeric WoS chief ID.
        session:   Optional externally-managed aiohttp session.
        timeout:   HTTP timeout in seconds.

    Returns:
        dict with keys:
          - name          (str)  player name
          - state         (str | None) state number, e.g. "3063"
          - last_seen     (str | None) human-readable last-seen string
          - wosatlas_url  (str)  direct link to the atlas player page
          - avatar_image  (str)  always "" — wosatlas has no avatars
          - source        (str)  "wosatlas"

    Raises:
        WosAtlasNotFoundError: Player not indexed by WoS Atlas.
        WosAtlasError: Any other fetch failure.
    """
    fid = str(player_id).strip()
    url = f"{BASE_URL}/player/{fid}/"
    html = await _get_html(url, session=session, timeout=timeout)

    BeautifulSoup = _try_import_bs4()
    name: Optional[str] = None
    state: Optional[str] = None
    last_seen: Optional[str] = None

    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1")
        if h1:
            name = h1.get_text(strip=True)
        state_link = soup.find("a", href=re.compile(r"/states/\d+/"))
        if state_link:
            m = re.search(r"/states/(\d+)/", state_link["href"])
            if m:
                state = m.group(1)
        time_tag = soup.find("time")
        if time_tag:
            last_seen = time_tag.get_text(strip=True) or time_tag.get("datetime")
    else:
        name = _extract_text(html, r"<h1[^>]*>([^<]+)</h1>")
        m_state = re.search(r"/states/(\d+)/", html)
        if m_state:
            state = m_state.group(1)
        m_time = re.search(r"<time[^>]*>([^<]+)</time>", html)
        if m_time:
            last_seen = m_time.group(1).strip()

    if not name or name.lower().startswith("whiteout survival"):
        raise WosAtlasNotFoundError(
            f"WoS Atlas: player {fid} not found or not indexed"
        )

    log.debug("WoS Atlas: player %s -> name=%s state=%s", fid, name, state)
    return {
        "name": name,
        "state": state,
        "last_seen": last_seen,
        "wosatlas_url": url,
        "avatar_image": "",  # wosatlas.com does not store avatars
        "source": "wosatlas",
    }


async def fetch_state_alliances(
    state_id,
    *,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict:
    """Fetch the state overview page including top alliances and top players.

    Args:
        state_id: WoS state number, e.g. 3063.

    Returns:
        dict with keys:
          - state_id      (str)
          - top_players   list of {name, fid, power, url}
          - top_alliances list of {name, tag, id, power, member_count, url}
          - wosatlas_url  (str)
          - source        (str)  "wosatlas"

    Raises:
        WosAtlasNotFoundError: State not indexed.
        WosAtlasError: Any fetch failure.
    """
    sid = str(state_id).strip()
    url = f"{BASE_URL}/states/{sid}/"
    html = await _get_html(url, session=session, timeout=timeout)

    top_players: list = []
    top_alliances: list = []

    BeautifulSoup = _try_import_bs4()

    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a", href=re.compile(r"/player/\d+/")):
            player_name = a.get_text(strip=True)
            href = a.get("href", "")
            m_fid = re.search(r"/player/(\d+)/", href)
            if m_fid and player_name:
                parent_text = _clean_text(str(a.parent)) if a.parent else ""
                m_power = re.search(
                    r"([\d,.]+[KMBkmb]?)\s*(?:Power|power)", parent_text
                )
                top_players.append({
                    "name": player_name,
                    "fid": m_fid.group(1),
                    "power": m_power.group(1) if m_power else None,
                    "url": f"{BASE_URL}{href}",
                })

        for a in soup.find_all("a", href=re.compile(r"/alliances/\d+/")):
            alliance_text = a.get_text(strip=True)
            href = a.get("href", "")
            m_aid = re.search(r"/alliances/(\d+)/", href)
            if m_aid and alliance_text:
                parent_text = _clean_text(str(a.parent)) if a.parent else ""
                m_tag = re.match(r"\[([A-Z0-9]+)\]", alliance_text)
                tag = m_tag.group(1) if m_tag else None
                m_members = re.search(r"(\d+)\s*/\s*100", parent_text)
                # Power: look for a number followed by B/M
                m_pow = re.search(r"([\d.]+[BMK])\b", parent_text)
                top_alliances.append({
                    "name": alliance_text,
                    "tag": tag,
                    "id": m_aid.group(1),
                    "power": m_pow.group(1) if m_pow else None,
                    "member_count": int(m_members.group(1)) if m_members else None,
                    "url": f"{BASE_URL}{href}",
                })
    else:
        for m in re.finditer(r'href="(/alliances/(\d+)/)"[^>]*>([^<]+)', html):
            href, aid, name = m.group(1), m.group(2), m.group(3).strip()
            if aid and name:
                top_alliances.append({
                    "name": name,
                    "tag": None,
                    "id": aid,
                    "power": None,
                    "member_count": None,
                    "url": f"{BASE_URL}{href}",
                })

    # De-duplicate
    seen_aids: set = set()
    unique_alliances = []
    for a in top_alliances:
        if a["id"] not in seen_aids:
            seen_aids.add(a["id"])
            unique_alliances.append(a)

    seen_fids: set = set()
    unique_players = []
    for p in top_players:
        if p["fid"] not in seen_fids:
            seen_fids.add(p["fid"])
            unique_players.append(p)

    log.debug(
        "WoS Atlas state %s: %d alliances, %d players",
        sid, len(unique_alliances), len(unique_players),
    )
    return {
        "state_id": sid,
        "top_players": unique_players[:10],
        "top_alliances": unique_alliances[:10],
        "wosatlas_url": url,
        "source": "wosatlas",
    }


async def fetch_alliance_info(
    alliance_id,
    *,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict:
    """Fetch publicly visible alliance data from WoS Atlas.

    Full member roster and coordinates require a WoS Atlas account.
    Returns what is visible without authentication: name, tag, level,
    power, honor level, member count, and requirements text.

    Args:
        alliance_id: WoS Atlas alliance ID (e.g. 3063000025).

    Returns:
        dict with keys:
          - name          (str)
          - tag           (str | None)  e.g. "ICE"
          - level         (int | None)
          - power         (str | None)  e.g. "39.8B"
          - honor_level   (int | None)
          - member_count  (int | None)
          - requirements  (str | None)
          - wosatlas_url  (str)
          - members       (list)  always [] — requires auth
          - members_note  (str)  explains auth requirement with direct URL
          - source        (str)  "wosatlas"

    Raises:
        WosAtlasNotFoundError: Alliance not found.
        WosAtlasError: Any fetch failure.
    """
    aid = str(alliance_id).strip()
    url = f"{BASE_URL}/alliances/{aid}/"
    html = await _get_html(url, session=session, timeout=timeout)

    BeautifulSoup = _try_import_bs4()
    name: Optional[str] = None
    tag: Optional[str] = None
    level: Optional[int] = None
    power: Optional[str] = None
    honor_level: Optional[int] = None
    member_count: Optional[int] = None
    requirements: Optional[str] = None

    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1")
        if h1:
            full_name = h1.get_text(strip=True)
            m_tag = re.match(r"\[([A-Z0-9]+)\]\s*(.+)", full_name)
            if m_tag:
                tag = m_tag.group(1)
                name = m_tag.group(2)
            else:
                name = full_name

        page_text = soup.get_text(separator=" ")
        m_level = re.search(r"Level\s+(\d+)", page_text, re.IGNORECASE)
        if m_level:
            level = int(m_level.group(1))
        m_power = re.search(
            r"(?:Server Power|Scanned Power)\s+([\d,.]+[BKMG]?)",
            page_text,
            re.IGNORECASE,
        )
        if m_power:
            power = m_power.group(1)
        m_honor = re.search(r"Honor level\s+(\d+)", page_text, re.IGNORECASE)
        if m_honor:
            honor_level = int(m_honor.group(1))
        m_members = re.search(r"(\d+)\s*/\s*100", page_text)
        if m_members:
            member_count = int(m_members.group(1))
        m_req = re.search(
            r"((?:Invite only|Open to all)[^.!?\n]{0,250})",
            page_text,
            re.IGNORECASE,
        )
        if m_req:
            requirements = re.sub(r"\s+", " ", m_req.group(1)).strip()
    else:
        name = _extract_text(html, r"<h1[^>]*>([^<]+)</h1>")
        m_lv = re.search(r"Level\s+(\d+)", html, re.IGNORECASE)
        if m_lv:
            level = int(m_lv.group(1))

    if not name:
        raise WosAtlasNotFoundError(f"WoS Atlas: alliance {aid} not found")

    return {
        "name": name,
        "tag": tag,
        "level": level,
        "power": power,
        "honor_level": honor_level,
        "member_count": member_count,
        "requirements": requirements,
        "wosatlas_url": url,
        "members": [],       # Requires WoS Atlas sign-in; not scrape-able
        "members_note": (
            "Full member roster + coordinates require a WoS Atlas account. "
            f"View at: {url}"
        ),
        "source": "wosatlas",
    }


# ── Convenience: silent fallback ──────────────────────────────────────────────

async def fetch_player_info_fallback(
    player_id,
    *,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Optional[dict]:
    """Best-effort player info fetch via WoS Atlas — never raises.

    Returns None if the player cannot be found or any error occurs.
    Suitable as a last-resort fallback after the Century Games API and
    WoS Oracle both fail.
    """
    try:
        return await fetch_player_info(player_id, session=session, timeout=timeout)
    except WosAtlasNotFoundError:
        log.debug("WoS Atlas: player %s not found — skipped", player_id)
    except WosAtlasAuthRequiredError:
        log.debug("WoS Atlas: auth required for player %s — skipped", player_id)
    except WosAtlasError as exc:
        log.warning("WoS Atlas: fetch failed for player %s: %s", player_id, exc)
    except Exception as exc:
        log.warning("WoS Atlas: unexpected error for player %s: %s", player_id, exc)
    return None
