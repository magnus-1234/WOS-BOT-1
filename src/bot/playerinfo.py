"""Compatibility shim re-exporting `cogs.playerinfo`.

This module keeps the original `playerinfo` import path working while the
implementation has moved to `cogs/playerinfo.py`.
"""

from cogs import playerinfo as _impl

# Re-export commonly used symbols
try:
    PlayerInfoCog = _impl.PlayerInfoCog
except Exception:
    PlayerInfoCog = None

try:
    map_furnace = _impl.map_furnace
except Exception:
    map_furnace = None

async def setup(bot):
    """Delegate setup to the moved implementation."""
    await _impl.setup(bot)

__all__ = ["setup", "PlayerInfoCog", "map_furnace"]
