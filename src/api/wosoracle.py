"""Async client for player profiles on WoS Oracle (https://wosoracle.com).

Authentication modes (in priority order):
  1. Bearer token  — set ``WOSORACLE_API_TOKEN`` in .env (requires Oracle+ plan).
     Rate limit: 5/sec · 50/min · 1,000 req/day.
  2. Session cookie — set ``WOSORACLE_SESSION_COOKIE`` in .env with the raw
     cookie string copied from a logged-in Discord browser session (free).

Confirmed endpoint (network-inspected 2026-09-26):
    GET https://wosoracle.com/api/v1/players/{player_id}
    Authorization: Bearer <token>        (paid Oracle+ plan)
    — OR —
    Cookie: <session cookie string>      (free, Discord-login session)

HTTP error codes:
  401 / 481  invalid or missing token
  402        subscription lapsed or route not covered by plan
  404        player not found
  429        rate-limited  (honour Retry-After header)
  5xx        server error  (auto-retried up to max_retries times)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://wosoracle.com"
_PLAYER_ENDPOINT = "/api/v1/players/{fid}"
_ALLIANCE_MEMBERS_ENDPOINT = "/alliance/{kid}/{alliance_id}"
PLAYER_ID_RE = re.compile(r"^\d{6,12}$")
_BROWSER_SERVICE_URL = os.getenv(
    "WOSORACLE_BROWSER_SERVICE_URL", "http://127.0.0.1:8765"
).strip().rstrip("/")


async def fetch_alliance_members(
    kingdom_id: str | int,
    alliance_id: str | int,
    *,
    browser_service_url: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch the authenticated, current roster through the signed-in browser bridge."""
    kid = str(kingdom_id).strip()
    aid = str(alliance_id).strip()
    if not re.fullmatch(r"\d{1,5}", kid) or not re.fullmatch(r"\d{6,12}", aid):
        raise ValueError("Invalid kingdom or alliance ID")
    service_url = (browser_service_url if browser_service_url is not None else _BROWSER_SERVICE_URL).strip().rstrip("/")
    if not service_url:
        raise WoSOracleAuthenticationError("Set WOSORACLE_BROWSER_SERVICE_URL for alliance roster lookups")
    url = f"{service_url}{_ALLIANCE_MEMBERS_ENDPOINT.format(kid=kid, alliance_id=aid)}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(url, headers={"Accept": "application/json"}) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise WoSOracleError("WoS Oracle returned an unexpected alliance response")
                    return data
                detail = (await response.text())[:200].strip()
                if response.status in (401, 403):
                    raise WoSOracleAuthenticationError(detail or "WoS Oracle browser session expired")
                if response.status == 429:
                    raise WoSOracleRateLimitError(detail or "WoS Oracle rate limit reached")
                raise WoSOracleError(f"WoS Oracle alliance lookup failed ({response.status}): {detail}")
    except aiohttp.ClientError as exc:
        raise WoSOracleError(f"Cannot connect to WoS Oracle browser service: {exc}") from exc


# ── Exceptions ────────────────────────────────────────────────────────────────

class WoSOracleError(RuntimeError):
    """Base exception for all WoS Oracle failures."""


class WoSOracleAuthenticationError(WoSOracleError):
    """Raised when WoS Oracle rejects or is missing credentials (401, 402, 481)."""


class WoSOraclePlayerNotFoundError(WoSOracleError):
    """Raised when the requested player is not in WoS Oracle's database (404)."""


class WoSOracleRateLimitError(WoSOracleError):
    """Raised on HTTP 429.  Check ``.retry_after`` for seconds to wait."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# ── Client class ──────────────────────────────────────────────────────────────

class WoSOracleClient:
    """Async WoS Oracle client supporting Bearer token or session-cookie auth.

    Recommended usage::

        async with WoSOracleClient() as client:
            player = await client.fetch_player(493368385)
            print(player["name"], player["power"])

    Config via environment variables:
        WOSORACLE_API_TOKEN      — Bearer token (Oracle+ subscription)
        WOSORACLE_SESSION_COOKIE — raw cookie string from a logged-in browser
    """

    def __init__(
        self,
        *,
        api_token: str | None = None,
        session_cookie: str | None = None,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        max_retries: int = 2,
    ) -> None:
        self._api_token = (api_token or os.getenv("WOSORACLE_API_TOKEN", "")).strip()
        self._session_cookie = (
            session_cookie or os.getenv("WOSORACLE_SESSION_COOKIE", "")
        ).strip()
        self._browser_service_url = _BROWSER_SERVICE_URL
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "WoSOracleClient":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
            headers={"User-Agent": "WhiteoutSurvivalBot/1.0 (+https://whiteoutsurvival.dev)"},
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── internals ─────────────────────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._browser_service_url:
            return headers
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        elif self._session_cookie:
            headers["Cookie"] = self._session_cookie
            headers["Referer"] = f"{self._base_url}/"
            headers["Origin"] = self._base_url
        else:
            raise WoSOracleAuthenticationError(
                "Set WOSORACLE_API_TOKEN (Bearer token, Oracle+) or "
                "WOSORACLE_SESSION_COOKIE (Discord session cookie) in your .env"
            )
        return headers

    @staticmethod
    def _validate_id(player_id: str | int) -> str:
        fid = str(player_id).strip()
        if not PLAYER_ID_RE.fullmatch(fid):
            raise ValueError(f"Invalid player ID {fid!r}: must be 6-12 digits")
        return fid

    # ── public API ────────────────────────────────────────────────────────────

    async def fetch_player(
        self,
        player_id: str | int,
        *,
        cached: bool = False,
        extra_session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any]:
        """Fetch a single player profile by WoS chief ID.

        Args:
            player_id: Numeric WoS chief ID (e.g. 493368385).
            cached:    Add ``?cached=1`` — returns stored data, skips live refresh.
            extra_session: Externally-managed ``aiohttp.ClientSession`` to reuse.

        Returns:
            Raw JSON dict from WoS Oracle for this player.

        Raises:
            ValueError: Bad player ID format.
            WoSOracleAuthenticationError: Missing / rejected credentials.
            WoSOraclePlayerNotFoundError: Player not in WoS Oracle's DB.
            WoSOracleRateLimitError: Rate-limit hit; check ``.retry_after``.
            WoSOracleError: Any other API / network failure.
        """
        fid = self._validate_id(player_id)
        if self._browser_service_url:
            url = f"{self._browser_service_url}/player/{fid}"
        else:
            url = f"{self._base_url}{_PLAYER_ENDPOINT.format(fid=fid)}"
        if cached:
            url += "?cached=1"
        headers = self._build_headers()

        owns_session = extra_session is None and self._session is None
        client: aiohttp.ClientSession = (
            extra_session
            or self._session
            or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout))
        )

        attempt = 0
        last_exc: Exception | None = None
        try:
            while attempt <= self._max_retries:
                attempt += 1
                try:
                    async with client.get(url, headers=headers) as resp:
                        status = resp.status

                        if status == 200:
                            try:
                                data = await resp.json(content_type=None)
                            except (aiohttp.ContentTypeError, ValueError) as exc:
                                raise WoSOracleError(
                                    "WoS Oracle returned non-JSON"
                                ) from exc
                            if not isinstance(data, dict):
                                raise WoSOracleError(
                                    "WoS Oracle returned unexpected response shape"
                                )
                            log.debug("WoSOracle: player %s fetched OK", fid)
                            return data

                        if status in (401, 481):
                            detail = (await resp.text()).strip()
                            raise WoSOracleAuthenticationError(
                                detail or f"WoS Oracle: invalid/expired credentials ({status})"
                            )
                        if status == 402:
                            detail = (await resp.text()).strip()
                            raise WoSOracleAuthenticationError(
                                detail or "WoS Oracle: Oracle+ subscription required (402)"
                            )
                        if status == 404:
                            raise WoSOraclePlayerNotFoundError(
                                f"Player {fid} not found on WoS Oracle"
                            )
                        if status == 429:
                            retry_after: float | None = None
                            try:
                                retry_after = float(resp.headers.get("Retry-After", ""))
                            except (ValueError, TypeError):
                                pass
                            raise WoSOracleRateLimitError(
                                f"WoS Oracle rate limit hit for player {fid}",
                                retry_after=retry_after,
                            )
                        if status >= 500:
                            detail = (await resp.text())[:200].strip()
                            last_exc = WoSOracleError(
                                f"WoS Oracle server error {status}: {detail}"
                            )
                            if attempt <= self._max_retries:
                                wait = 2.0 ** attempt
                                log.warning(
                                    "WoSOracle HTTP %s — retry %d/%d in %.1fs",
                                    status, attempt, self._max_retries + 1, wait,
                                )
                                await asyncio.sleep(wait)
                                continue
                            raise last_exc  # type: ignore[misc]

                        resp.raise_for_status()

                except (aiohttp.ServerTimeoutError, asyncio.TimeoutError) as exc:
                    last_exc = WoSOracleError(f"WoS Oracle request timed out: {exc}")
                    if attempt <= self._max_retries:
                        await asyncio.sleep(1.5 ** attempt)
                        continue
                    raise last_exc from exc

                except aiohttp.ClientConnectorError as exc:
                    raise WoSOracleError(f"Cannot connect to WoS Oracle: {exc}") from exc

            raise last_exc or WoSOracleError("WoS Oracle request failed after retries")
        finally:
            if owns_session:
                await client.close()

    async def fetch_players_bulk(
        self,
        player_ids: list[str | int],
        *,
        cached: bool = False,
        concurrency: int = 3,
        delay_between: float = 0.25,
    ) -> dict[str, dict[str, Any]]:
        """Fetch multiple players concurrently with built-in rate-limit handling.

        Args:
            player_ids:     List of chief IDs to look up.
            cached:         Use ``?cached=1`` on each request.
            concurrency:    Max simultaneous requests (Oracle+ allows 5/sec).
            delay_between:  Seconds between each request batch.

        Returns:
            Dict of ``str(player_id)`` → profile dict.  Failed players are
            omitted (errors logged as warnings).
        """
        semaphore = asyncio.Semaphore(concurrency)
        results: dict[str, dict[str, Any]] = {}

        async def _one(pid: str | int) -> None:
            fid = str(pid).strip()
            async with semaphore:
                try:
                    results[fid] = await self.fetch_player(fid, cached=cached)
                except WoSOraclePlayerNotFoundError:
                    log.warning("WoSOracle: player %s not found — skipped", fid)
                except WoSOracleRateLimitError as exc:
                    wait = exc.retry_after or 60.0
                    log.warning("WoSOracle rate limit — sleeping %.1fs", wait)
                    await asyncio.sleep(wait)
                    try:
                        results[fid] = await self.fetch_player(fid, cached=cached)
                    except WoSOracleError as retry_exc:
                        log.error("WoSOracle retry failed for %s: %s", fid, retry_exc)
                except WoSOracleError as exc:
                    log.warning("WoSOracle fetch failed for %s: %s", fid, exc)
                await asyncio.sleep(delay_between)

        await asyncio.gather(*(_one(pid) for pid in player_ids))
        return results


# ── Backwards-compatible module-level helper ──────────────────────────────────

async def fetch_player_info(
    player_id: str | int,
    *,
    api_token: str | None = None,
    session_cookie: str | None = None,
    session: aiohttp.ClientSession | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """One-shot player fetch — thin wrapper around ``WoSOracleClient``.

    Auth priority: ``api_token`` arg → ``WOSORACLE_API_TOKEN`` env →
    ``session_cookie`` arg → ``WOSORACLE_SESSION_COOKIE`` env.
    """
    client = WoSOracleClient(
        api_token=api_token,
        session_cookie=session_cookie,
        timeout=timeout,
    )
    return await client.fetch_player(player_id, extra_session=session)
