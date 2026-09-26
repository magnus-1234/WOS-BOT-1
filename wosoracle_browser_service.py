"""Serve WoSOracle lookups through a persistent, signed-in Chromium profile.

The browser owns the OAuth cookies and short-lived API tokens. They are not
returned to, or written by, the Discord bot. The HTTP bridge binds to loopback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright


LOG = logging.getLogger("wosoracle.browser")
PLAYER_ID_RE = re.compile(r"^\d{8,12}$")
KINGDOM_ID_RE = re.compile(r"^\d{1,5}$")
ALLIANCE_ID_RE = re.compile(r"^\d{6,12}$")
ORIGIN = "https://wosoracle.com"
DEFAULT_PROFILE_DIR = Path.home() / ".wosoracle-local-profile"
PROFILE_DIR = Path(os.environ.get("WOSORACLE_PROFILE_DIR", str(DEFAULT_PROFILE_DIR)))
CDP_HTTP_PORT = 8765
BROWSER_NAME = os.environ.get("WOSORACLE_BROWSER", "chromium").strip().lower()


class BrowserSession:
    def __init__(self) -> None:
        self.playwright = None
        self.context = None
        self.page = None
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        self.playwright = await async_playwright().start()
        viewport = {"width": 1280, "height": 900}
        if BROWSER_NAME == "firefox":
            self.context = await self.playwright.firefox.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                viewport=viewport,
            )
        elif BROWSER_NAME == "chromium":
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-background-networking",
                    "--disable-features=Translate,MediaRouter",
                    "--window-size=1280,900",
                    "--start-maximized",
                ],
                viewport=viewport,
            )
        else:
            raise RuntimeError(f"Unsupported WOSORACLE_BROWSER: {BROWSER_NAME}")
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        try:
            await self.page.goto(ORIGIN, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            # Keep the browser open for interactive sign-in even if the site is
            # temporarily unavailable during startup.
            LOG.warning("Could not load WoSOracle at startup: %s", exc)
        LOG.info("WoSOracle %s browser ready; persistent profile is %s", BROWSER_NAME, PROFILE_DIR)

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()

    async def authenticated(self) -> bool:
        if not self.page:
            return False
        try:
            return bool(await self.page.evaluate("""async () => {
              const r = await window.fetch('/api/auth/me', {
                headers: {Accept: 'application/json'}, credentials: 'include', cache: 'no-store'
              });
              if (!r.ok) return false;
              const data = await r.json();
              return !!data.authenticated;
            }"""))
        except Exception:
            return False

    async def player(self, fid: str) -> tuple[int, dict | str]:
        if not self.page:
            return 503, "Browser is starting"
        async with self.lock:
            try:
                result = await self.page.evaluate("""async (fid) => {
                  const r = await window.fetch(`/api/players/${encodeURIComponent(fid)}`, {
                    headers: {Accept: 'application/json'}, credentials: 'include', cache: 'no-store'
                  });
                  const text = await r.text();
                  let body;
                  try { body = JSON.parse(text); } catch { body = {error: text}; }
                  return {status: r.status, body};
                }""", fid)
                return int(result["status"]), result["body"]
            except Exception as exc:
                LOG.warning("WoSOracle lookup failed for %s: %s", fid, exc)
                return 502, "WoSOracle browser request failed"

    async def alliance(self, kid: str, alliance_id: str) -> tuple[int, dict | str]:
        if not self.page:
            return 503, "Browser is starting"
        async with self.lock:
            try:
                result = await self.page.evaluate("""async ({kid, allianceId}) => {
                  const r = await window.fetch(
                    `/api/alliances/${encodeURIComponent(allianceId)}?kid=${encodeURIComponent(kid)}`,
                    {headers: {Accept: 'application/json'}, credentials: 'include', cache: 'no-store'}
                  );
                  const text = await r.text();
                  let body;
                  try { body = JSON.parse(text); } catch { body = {error: text}; }
                  return {status: r.status, body};
                }""", {"kid": kid, "allianceId": alliance_id})
                return int(result["status"]), result["body"]
            except Exception as exc:
                LOG.warning("WoSOracle alliance lookup failed for %s/%s: %s", kid, alliance_id, exc)
                return 502, "WoSOracle browser request failed"


async def health(request: web.Request) -> web.Response:
    browser: BrowserSession = request.app["browser"]
    return web.json_response({"ready": browser.page is not None, "signed_in": await browser.authenticated()})


async def player(request: web.Request) -> web.Response:
    fid = request.match_info["fid"]
    if not PLAYER_ID_RE.fullmatch(fid):
        raise web.HTTPBadRequest(text="Player ID must contain 8 to 12 digits")
    browser: BrowserSession = request.app["browser"]
    if not await browser.authenticated():
        raise web.HTTPUnauthorized(text="Sign in to WoSOracle in the VM browser window")
    status, body = await browser.player(fid)
    if status in (401, 403):
        raise web.HTTPUnauthorized(text="WoSOracle session expired; sign in again in the VM browser window")
    if status == 404:
        raise web.HTTPNotFound(text="Player not found in WoSOracle")
    if status == 429:
        raise web.HTTPTooManyRequests(text="WoSOracle rate limit reached; retry later")
    if status >= 400:
        raise web.HTTPBadGateway(text=f"WoSOracle returned HTTP {status}")
    return web.json_response(body)


async def alliance(request: web.Request) -> web.Response:
    kid = request.match_info["kid"]
    alliance_id = request.match_info["alliance_id"]
    if not KINGDOM_ID_RE.fullmatch(kid) or not ALLIANCE_ID_RE.fullmatch(alliance_id):
        raise web.HTTPBadRequest(text="Invalid kingdom or alliance ID")
    browser: BrowserSession = request.app["browser"]
    if not await browser.authenticated():
        raise web.HTTPUnauthorized(text="Sign in to WoSOracle in the VM browser window")
    status, body = await browser.alliance(kid, alliance_id)
    if status in (401, 403):
        raise web.HTTPUnauthorized(text="WoSOracle session expired; sign in again in the VM browser window")
    if status == 404:
        raise web.HTTPNotFound(text="Alliance not found in WoSOracle")
    if status == 429:
        raise web.HTTPTooManyRequests(text="WoSOracle rate limit reached; retry later")
    if status >= 400:
        raise web.HTTPBadGateway(text=f"WoSOracle returned HTTP {status}")
    return web.json_response(body)


async def create_app() -> web.Application:
    browser = BrowserSession()
    await browser.start()
    app = web.Application()
    app["browser"] = browser
    app.router.add_get("/health", health)
    app.router.add_get("/player/{fid}", player)
    app.router.add_get("/alliance/{kid}/{alliance_id}", alliance)
    app.on_cleanup.append(lambda _app: browser.close())
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    web.run_app(create_app(), host="127.0.0.1", port=CDP_HTTP_PORT, access_log=None)
