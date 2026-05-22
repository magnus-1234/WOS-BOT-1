import asyncio
from typing import List, Dict

try:
    from duckduckgo_search import DDGS
except Exception:
    DDGS = None


def _format_results(results: List[Dict], max_chars: int = 1500) -> str:
    parts = []
    total = 0
    for r in results:
        title = r.get("title") or r.get("text") or "(no title)"
        href = r.get("href") or r.get("url") or r.get("link") or ""
        snippet = r.get("body") or r.get("snippet") or ""
        entry = f"- {title}: {snippet} ({href})"
        if total + len(entry) > max_chars:
            break
        parts.append(entry)
        total += len(entry)
    return "\n".join(parts)


async def fetch_search_results(query: str, max_results: int = 3) -> List[Dict]:
    """Fetch search results using duckduckgo-search in an executor.

    Returns a list of result dicts (may be empty). If the library isn't
    available, returns an empty list.
    """
    if DDGS is None:
        return []

    loop = asyncio.get_event_loop()

    def _sync_search(q, mx):
        try:
            with DDGS() as ddgs:
                return ddgs.text(q, max_results=mx)
        except Exception:
            return []

    try:
        results = await loop.run_in_executor(None, lambda: _sync_search(query, max_results))
        return results or []
    except Exception:
        return []


def inject_results_into_system(messages: List[Dict], results: List[Dict], header: str = "Web Search Results:", max_chars: int = 1200) -> List[Dict]:
    """Return a copy of messages with results appended to the system message.

    - Keeps messages immutable by making a shallow copy of the list and the first dict.
    - Truncates concatenated result text to `max_chars` characters to avoid very long prompts.
    """
    if not messages:
        return messages

    # Format selected results into text
    formatted = _format_results(results, max_chars=max_chars)
    if not formatted:
        return messages

    # Make shallow copies to avoid mutating caller data structures
    new_messages = list(messages)
    system = dict(new_messages[0])
    system_content = system.get("content", "")

    injection = f"\n\n{header}\n{formatted}\n\n"
    # Ensure total appended length small
    if len(injection) > max_chars:
        injection = injection[:max_chars]

    system["content"] = system_content + injection
    new_messages[0] = system
    return new_messages
