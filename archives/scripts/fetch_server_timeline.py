"""
fetch_server_timeline.py

Usage:
  python fetch_server_timeline.py 3063

What it does:
- GETs https://whiteoutsurvival.pl/state-timeline/ to try to extract a nonce
- POSTs form data to https://whiteoutsurvival.pl/wp-admin/admin-ajax.php with:
    action=stp_get_timeline
    nonce=<extracted or provided>
    server_id=<server id>
- Attempts to parse JSON response; if not JSON, prints a short parsed summary or raw HTML

Notes:
- Nonces can be short-lived. If the script cannot find a nonce, you can pass one manually.
- This script uses aiohttp (async). If you prefer curl, an example PowerShell curl is included below.

Example PowerShell curl (replace nonce if required):
  curl "https://whiteoutsurvival.pl/wp-admin/admin-ajax.php" -Method POST -Body @{
    action = 'stp_get_timeline'
    nonce = '63c5db18ad'
    server_id = '3063'
  } -Headers @{ 'X-Requested-With' = 'XMLHttpRequest'; 'Referer' = 'https://whiteoutsurvival.pl/state-timeline/'}

"""

import asyncio
import aiohttp
import re
import json
import sys
from typing import Optional

# use the parser module to produce structured output suitable for embeds
from server_timeline_parser import parse_response, format_for_embed

BASE = "https://whiteoutsurvival.pl"
TIMELINE_PAGE = BASE + "/state-timeline/"
AJAX = BASE + "/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": TIMELINE_PAGE,
    "X-Requested-With": "XMLHttpRequest",
}

async def get_nonce(session: aiohttp.ClientSession) -> Optional[str]:
    """Try to extract a nonce from the timeline page. Returns None if not found."""
    try:
        async with session.get(TIMELINE_PAGE, timeout=15) as resp:
            text = await resp.text()
    except Exception as e:
        print(f"[!] Failed to GET timeline page: {e}")
        return None

    # Try several heuristics to find a nonce value
    # 1) JSON format: "nonce":"63c5db18ad"
    m = re.search(r'"(?:stp_nonce|nonce|wp_nonce)"\s*:\s*"([a-f0-9]{6,})"', text, re.I)
    if m:
        return m.group(1)
    
    # 2) name="nonce" value="..."
    m = re.search(r'name=["\']?nonce["\']?\s+value=["\']([^"\']+)["\']', text)
    if m:
        return m.group(1)

    # 3) data-nonce or data_nonce
    m = re.search(r'data-nonce=["\']([^"\']+)["\']', text)
    if m:
        return m.group(1)

    # 4) javascript assignment: nonce: '63c5db18ad' or "nonce":"..."
    m = re.search(r'(?:stp_nonce|nonce|wp_nonce|_ajax_nonce)\s*[:=]\s*["\']([a-f0-9]{6,})["\']', text, re.I)
    if m:
        return m.group(1)

    # If nothing found
    return None


def try_extract_day_from_text(text: str):
    """Look for likely day/open date markers in the response text."""
    # look for patterns like 'Day 50', 'Dzień 50', 'day: 50', 'open date'
    m = re.search(r'Day\s*(\d{1,5})', text, re.I)
    if not m:
        m = re.search(r'Dzie[nń]\s*(\d{1,5})', text, re.I)
    if not m:
        m = re.search(r'\bday[:\s]*?(\d{1,5})\b', text, re.I)
    if m:
        return ("day", int(m.group(1)))

    # look for dates YYYY-MM-DD or DD.MM.YYYY
    m = re.search(r'([0-9]{4}-[0-9]{2}-[0-9]{2})', text)
    if m:
        return ("date", m.group(1))
    m = re.search(r'([0-9]{2}\.[0-9]{2}\.[0-9]{4})', text)
    if m:
        return ("date", m.group(1))

    return None


async def fetch_timeline(server_id: str, provided_nonce: Optional[str] = None, mode: str = 'full'):
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        nonce = provided_nonce
        if not nonce:
            print("[i] Attempting to fetch nonce from state-timeline page...")
            nonce = await get_nonce(session)
            if nonce:
                print(f"[i] Found nonce: {nonce}")
            else:
                print("[!] No nonce found on page. We will still try to POST but the request may fail if a nonce is required.")

        data = {
            "action": "stp_get_timeline",
            "nonce": nonce or "",
            "server_id": str(server_id),
        }

        try:
            async with session.post(AJAX, data=data, timeout=15) as resp:
                text = await resp.text()
                status = resp.status
        except Exception as e:
            print(f"[!] POST request failed: {e}")
            return

        # Always print status in case of error, even in line mode
        if status != 200:
            print(f"[!] POST {AJAX} -> status {status}")
            if status == 403:
                print("[!] Access denied (403). The nonce might be required or your IP is rate-limited.")
                print(f"[!] Response: {text[:200]}")
            return
        
        # Only print verbose output if NOT in line mode
        if mode != 'line':
            print(f"[i] POST {AJAX} -> status {status}\n")

        # Use the integrated parser to normalize the response into a structured mapping
        structured = None

        # try JSON first
        try:
            parsed = json.loads(text)
            if mode != 'line':
                print("[i] JSON response parsed (pretty):\n")
                print(json.dumps(parsed, indent=2, ensure_ascii=False))
            structured = parse_response(parsed, server_id=server_id, compact=True)
        except json.JSONDecodeError:
            # not JSON — try parsing as HTML/text
            structured = parse_response(text, server_id=server_id, compact=True)

        # Print structured mapping and an embed preview
        # Build a minimal filtered report (only useful fields)
        def build_minimal(parsed: dict) -> dict:
            out = {
                'server_id': parsed.get('server_id'),
                'days': parsed.get('days'),
            }
            if parsed.get('active_text'):
                # prefer the full sentence if available
                out['active_text'] = parsed.get('active_line') or parsed.get('active_text')
            if parsed.get('open_date'):
                out['open_date'] = parsed.get('open_date')

            # find next milestone
            next_ms = None
            recent = []
            ms = parsed.get('milestones') or []
            try:
                current = int(parsed.get('days')) if parsed.get('days') is not None else None
            except Exception:
                current = None

            for m in ms:
                if not isinstance(m, dict):
                    continue
                day = m.get('day')
                if isinstance(day, int):
                    if current is None or day > current:
                        if next_ms is None:
                            next_ms = {'day': day, 'title': m.get('title'), 'desc': m.get('desc', '')}
                    if current is None or day <= (current or 0):
                        recent.append({'day': day, 'title': m.get('title')})

            # recent: last 3 sorted desc
            recent_sorted = sorted(recent, key=lambda x: x['day'], reverse=True)[:3]
            if next_ms:
                out['next_milestone'] = next_ms
            if recent_sorted:
                out['recent_milestones'] = recent_sorted
            return out

        minimal = build_minimal(structured or {})

        # If mode == 'line', print only the active sentence and exit
        if mode == 'line':
            active_line = (structured or {}).get('active_line') or (structured or {}).get('active_text')
            if active_line:
                print(active_line)
            return

        # Otherwise, print full output
        print("\n[i] Filtered summary (useful fields only):\n")
        try:
            print(json.dumps(minimal, indent=2, ensure_ascii=False))
        except Exception:
            print(minimal)

        # Also print a compact human-readable one-line summary
        summary_parts = [f"Server {minimal.get('server_id', '?')}"]
        if minimal.get('days') is not None:
            summary_parts.append(f"Day {minimal.get('days')}")
        if minimal.get('open_date'):
            summary_parts.append(f"Open: {minimal.get('open_date')}")
        print("\n[i] One-line summary:\n")
        print(' — '.join(summary_parts))

        embed = format_for_embed(structured or {})
        print("\n[i] Embed preview:\n")
        try:
            print(json.dumps(embed, indent=2, ensure_ascii=False))
        except Exception:
            print(embed)
        return


def print_usage_and_exit():
    print("Usage: python fetch_server_timeline.py <server_id> [nonce]")
    print("Example: python fetch_server_timeline.py 3063 63c5db18ad")
    sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print_usage_and_exit()
    server_id = sys.argv[1]
    provided_nonce = None
    mode = 'full'  # or 'line' to print only the active line

    # parse optional args: a nonce (hex-like) and flags like --line / -l
    for arg in sys.argv[2:]:
        if arg in ('--line', '--only-line', '-l', 'line'):
            mode = 'line'
            continue
        if re.fullmatch(r'[A-Fa-f0-9]{6,}', arg):
            provided_nonce = arg
            continue

    asyncio.run(fetch_timeline(server_id, provided_nonce, mode=mode))
