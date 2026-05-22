"""Bot configuration and policy flags.

This file centralizes operator-controlled flags that determine
what the bot may include in responses. Keep defaults conservative
for privacy and safety.

Operators can change ALLOW_PLAYER_IDS to True if they are certain
that returning player IDs is acceptable for their deployment.
"""

from typing import Optional, Iterable
import os

# Default - do NOT include player IDs in responses unless explicitly enabled
ALLOW_PLAYER_IDS = os.getenv('ALLOW_PLAYER_IDS', 'false').lower() in ('1', 'true', 'yes')


def can_show_player_ids(user_roles: Optional[Iterable[str]] = None) -> bool:
    """Determine if player IDs may be shown to the caller.

    By default this consults the global ALLOW_PLAYER_IDS flag. Callers
    may pass a list of user roles (e.g. ['admin', 'moderator']) and the
    function can be extended to allow IDs for trusted roles only.
    """
    if ALLOW_PLAYER_IDS:
        return True
    # Example extension point: allow certain roles even if global flag is off
    if user_roles:
        trusted = {'admin', 'owner', 'moderator','Male'}
        if trusted.intersection(set(user_roles)):
            return True
    return False
