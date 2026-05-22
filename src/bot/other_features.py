"""Compatibility shim re-exporting `cogs.other_features`.

This module keeps the original top-level import path `other_features` working
while the implementation lives in `cogs/other_features.py`.
"""

from cogs import other_features as _impl

try:
    OtherFeatures = _impl.OtherFeatures
except Exception:
    OtherFeatures = None

async def setup(bot):
    """Delegate setup to the moved implementation."""
    await _impl.setup(bot)

__all__ = ["setup", "OtherFeatures"]
