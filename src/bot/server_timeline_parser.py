"""
server_timeline_parser.py

Helpers to parse the response (JSON or HTML) from the site's `stp_get_timeline` AJAX call
and convert it into a structured mapping ready for Discord embed formatting.

Public functions:
- parse_response(raw, server_id=None) -> dict
- format_for_embed(struct) -> dict

The returned dict structure (example):
{
  "server_id": "3063",
  "days": 123,
  "open_date": "2025-09-15",
  "milestones": [ {"day": 120, "title": "Gen 2 Heroes", "desc": "..."}, ... ],
  "raw": "...full response..."
}

"""
from typing import Any, Dict, List, Optional, Union
import json
import re
from bs4 import BeautifulSoup


def _extract_from_json(obj: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    # Common fields
    if 'days' in obj:
        try:
            out['days'] = int(obj['days'])
        except Exception:
            out['days'] = obj.get('days')
    if 'open_date' in obj:
        out['open_date'] = obj['open_date']

    # Some endpoints return data inside 'data' key
    data = obj.get('data') if isinstance(obj.get('data'), (dict, list)) else None
    if isinstance(data, dict):
        for k in ('days', 'open_date', 'start_date', 'open_date_formatted'):
            if k in data and k not in out:
                out[k] = data[k]
        # sometimes HTML fragment is in data['html']
        if 'html' in data:
            out['html'] = data['html']

    # If the response contains a success flag and nested structure, try to be flexible
    # also consider top-level keys like 'timeline', 'content', 'result'
    for key in ('timeline', 'content', 'result', 'message'):
        val = obj.get(key)
        if isinstance(val, dict):
            # recursive extraction
            nested = _extract_from_json(val)
            for kk, vv in nested.items():
                if kk not in out:
                    out[kk] = vv
        elif isinstance(val, str) and not out.get('html'):
            out['html'] = val

    return out


def _extract_from_html(html: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    soup = BeautifulSoup(html, 'html.parser')

    # Try to find obvious day markers: "Day 50", "Dzień 50"
    txt = soup.get_text(separator=' ', strip=True)

    # First, try to extract the server info block that contains the active time and start date.
    # Example HTML snippet:
    # "This server has been active for <strong>140 days, 3 hours, 53 minutes</strong>.<br>It started on <strong>25/06/2025 - 11:15:02 UTC</strong>."
    try:
        server_info = soup.select_one('.stp-server-info') or soup.select_one('.stp-alert')
        if server_info:
            si_html = str(server_info)
            # full active text inside strong: keep it as human readable
            # also extract the whole sentence containing the phrase, e.g.:
            # "This server has been active for 140 days, 3 hours, 53 minutes."
            m_active = re.search(r'This server has been active for\s*<strong>([^<]+)</strong>', si_html, re.I)
            if m_active:
                active_text = m_active.group(1).strip()
                out['active_text'] = active_text
                # try to extract the full sentence from the server_info text
                try:
                    si_text = server_info.get_text(separator=' ', strip=True)
                    m_full = re.search(r'(This server has been active for[^.]+\.)', si_text, re.I)
                    if m_full:
                        out['active_line'] = m_full.group(1).strip()
                    else:
                        out['active_line'] = f"This server has been active for {active_text}."
                except Exception:
                    out['active_line'] = f"This server has been active for {active_text}."
                # find leading number of days
                m_days = re.search(r'(\d{1,5})\s+days', active_text, re.I)
                if m_days:
                    try:
                        out['days'] = int(m_days.group(1))
                    except Exception:
                        out['days'] = m_days.group(1)

            m_start = re.search(r'It started on\s*<strong>([^<]+)</strong>', si_html, re.I)
            if m_start:
                out['open_date'] = m_start.group(1).strip()
                # also capture the full 'It started on ...' sentence if possible
                try:
                    si_text = server_info.get_text(separator=' ', strip=True)
                    m_start_full = re.search(r'(It started on[^.]+\.)', si_text, re.I)
                    if m_start_full:
                        out['start_line'] = m_start_full.group(1).strip()
                except Exception:
                    pass
    except Exception:
        # ignore parsing errors and fall back to generic text heuristics below
        pass

    # Fallback: find patterns like 'Day 50' or 'Dzień 50' in the page text
    if 'days' not in out:
        m = re.search(r'Day\s*(\d{1,5})', txt, re.I) or re.search(r'Dzie[nń]\s*(\d{1,5})', txt, re.I)
        if m:
            try:
                out['days'] = int(m.group(1))
            except Exception:
                out['days'] = m.group(1)

    # Dates like 2025-09-15 or 15.09.2025
    m2 = re.search(r'([0-9]{4}-[0-9]{2}-[0-9]{2})', txt)
    if m2:
        out['open_date'] = m2.group(1)
    else:
        m3 = re.search(r'([0-9]{2}\.[0-9]{2}\.[0-9]{4})', txt)
        if m3:
            out['open_date'] = m3.group(1)

    # Find structured milestone entries in HTML: each .stp-event typically contains an <h4> title and a .stp-day-badge
    milestones: List[Dict[str, Any]] = []
    events = soup.find_all(class_=re.compile(r'stp-event', re.I))
    if not events:
        # try a more generic selector for items that look like timeline entries
        events = soup.select('.stp-event, .event, li, tr')

    for ev in events:
        try:
            header = ev.find('h4')
            title = header.get_text(strip=True) if header else ''
            # day badge
            badge = ev.select_one('.stp-day-badge')
            day = None
            if badge:
                bd_text = badge.get_text(strip=True)
                mday = re.search(r'(\d{1,5})', bd_text)
                if mday:
                    day = int(mday.group(1))
            # days-left text
            days_left_el = ev.select_one('.stp-days-left')
            days_left = days_left_el.get_text(strip=True) if days_left_el else ''
            # try to find a representative image inside the event (hero image, thumbnail)
            img_el = ev.find('img')
            img_src = None
            if img_el and img_el.has_attr('src'):
                img_src = img_el['src'].strip()
                # normalize protocol-relative and root-relative URLs
                if img_src.startswith('//'):
                    img_src = 'https:' + img_src
                elif img_src.startswith('/'):
                    img_src = 'https://whiteoutsurvival.pl' + img_src
            if day is not None:
                mobj: Dict[str, Any] = {'day': day, 'title': title, 'desc': days_left}
                if img_src:
                    mobj['image'] = img_src
                milestones.append(mobj)
        except Exception:
            continue

    if milestones:
        # dedupe by day and sort
        unique = {m['day']: m for m in milestones}
        ms = list(unique.values())
        ms.sort(key=lambda x: x['day'])
        out['milestones'] = ms

    return out


def parse_response(raw: Union[str, Dict[str, Any], List[Any]], server_id: Optional[Union[str, int]] = None, compact: bool = True) -> Dict[str, Any]:
    """Parse a raw response (JSON-decoded object or HTML string) and return a structured mapping.

    The returned mapping contains at least the keys:
      - server_id
      - days (optional)
      - open_date (optional)
      - milestones (optional list)
      - raw (original raw string if available)

    """
    result: Dict[str, Any] = {}
    if server_id is not None:
        result['server_id'] = str(server_id)

    # If already a dict (JSON parsed), dig for fields
    if isinstance(raw, dict):
        extracted = _extract_from_json(raw)
        result.update(extracted)
        # if html fragment present, attempt to parse it further
        if 'html' in extracted and isinstance(extracted['html'], str):
            out2 = _extract_from_html(extracted['html'])
            for k, v in out2.items():
                if k not in result:
                    result[k] = v
        if not compact:
            result['raw'] = json.dumps(raw, ensure_ascii=False)
        # if html fragment present, attempt to parse it further
        if 'html' in extracted and isinstance(extracted['html'], str):
            out2 = _extract_from_html(extracted['html'])
            for k, v in out2.items():
                if k not in result:
                    result[k] = v
        # remove html if compact
        if compact and 'html' in result:
            result.pop('html', None)
        # derive a top-level next_milestone_image if available
        if 'milestones' in result and isinstance(result['milestones'], list):
            try:
                cur = int(result.get('days')) if result.get('days') is not None else None
            except Exception:
                cur = None
            next_img = None
            for m in result['milestones']:
                try:
                    md = int(m.get('day'))
                    if cur is None or md > cur:
                        if isinstance(m.get('image'), str):
                            next_img = m.get('image')
                            break
                except Exception:
                    continue
            if next_img:
                result['next_milestone_image'] = next_img
        return result

    # If raw is a list, try to stringify
    if isinstance(raw, list):
        if not compact:
            try:
                result['raw'] = json.dumps(raw, ensure_ascii=False)
            except Exception:
                result['raw'] = str(raw)
        return result

    # Otherwise assume string HTML/text
    if isinstance(raw, str):
        if not compact:
            result['raw'] = raw
        # try to find JSON embedded in the text
        m = re.search(r'\{\s*"success".*\}', raw, re.S)
        if m:
            try:
                j = json.loads(m.group(0))
                extracted = _extract_from_json(j)
                result.update(extracted)
            except Exception:
                pass

        # Attempt HTML parsing
        parsed_html = _extract_from_html(raw)
        for k, v in parsed_html.items():
            if k not in result:
                result[k] = v

        # Final heuristic: look for 'Day 123' or a date
        if 'days' not in result:
            mday = re.search(r'\bDay\s*(\d{1,5})\b', raw, re.I) or re.search(r'\bDzie[nń]\s*(\d{1,5})\b', raw, re.I)
            if mday:
                try:
                    result['days'] = int(mday.group(1))
                except Exception:
                    result['days'] = mday.group(1)

        if 'open_date' not in result:
            mdate = re.search(r'([0-9]{4}-[0-9]{2}-[0-9]{2})', raw) or re.search(r'([0-9]{2}\.[0-9]{2}\.[0-9]{4})', raw)
            if mdate:
                result['open_date'] = mdate.group(1)

        # when compact, drop very large keys that are not needed
        if compact:
            result.pop('html', None)
            # keep raw only if it's small
            if 'raw' in result and len(str(result.get('raw', ''))) > 1000:
                result.pop('raw', None)

        # populate top-level next_milestone_image if possible
        if 'milestones' in result and isinstance(result['milestones'], list):
            try:
                cur = int(result.get('days')) if result.get('days') is not None else None
            except Exception:
                cur = None
            next_img = None
            for m in result['milestones']:
                try:
                    md = int(m.get('day'))
                    if cur is None or md > cur:
                        if isinstance(m.get('image'), str):
                            next_img = m.get('image')
                            break
                except Exception:
                    continue
            if next_img:
                result['next_milestone_image'] = next_img

        return result

    # fallback
    result['raw'] = str(raw)
    return result


def format_for_embed(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Convert parsed mapping into a dict representing a Discord embed-like structure.

    Example output:
    {
      'title': 'Server 3063 — Day 50',
      'description': 'Open date: 2025-09-15',
      'fields': [ {'name':'Next milestone', 'value':'Day 53: Sunfire Castle (in 3 days)'}, ... ]
    }
    """
    title = f"Server {parsed.get('server_id', '?')}"
    days = parsed.get('days')
    if days is not None:
        title = f"{title} — Day {days}"

    description_parts: List[str] = []
    if 'open_date' in parsed:
        description_parts.append(f"Open date: {parsed['open_date']}")
    if 'raw' in parsed and len(parsed.get('raw', '')) > 2000:
        # avoid huge raw in description
        description_parts.append("(raw response omitted)")

    fields: List[Dict[str, str]] = []
    if 'milestones' in parsed and isinstance(parsed['milestones'], list):
        # find the next milestone after current day
        ms = parsed['milestones']
        next_ms = None
        if days is not None:
            for m in ms:
                try:
                    if m.get('day') and int(m.get('day')) > int(days):
                        next_ms = m
                        break
                except Exception:
                    continue
        if next_ms:
            fields.append({'name': 'Next milestone', 'value': f"Day {next_ms.get('day')}: {next_ms.get('title')}"})

        # add recent milestones (last 3)
        recent = [m for m in ms if isinstance(m.get('day'), int) and (days is None or m['day'] <= days)]
        recent = sorted(recent, key=lambda x: x['day'], reverse=True)[:3]
        if recent:
            rec_lines = []
            for r in recent:
                rec_lines.append(f"Day {r.get('day')}: {r.get('title')}")
            fields.append({'name': 'Recent milestones', 'value': '\n'.join(rec_lines)})

    # fallback: if we don't have milestones, but we have days, show simple field
    if not fields and days is not None:
        fields.append({'name': 'Server Age', 'value': f"{days} days"})

    embed = {
        'title': title,
        'description': '\n'.join(description_parts) if description_parts else '',
        'fields': fields,
    }
    return embed
