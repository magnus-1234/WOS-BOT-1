"""Compatibility shim re-exporting `cogs.alliance`.

This keeps the original top-level import path `alliance` working while the
implementation lives in `cogs/alliance.py`.
"""

from cogs import alliance as _impl


try:
    Alliance = _impl.Alliance
except Exception:
    Alliance = None


async def setup(bot):
    """Delegate setup to the moved implementation."""
    # The real setup function lives in cogs.alliance; delegate to it so
    # existing calls to ``load_extension('alliance')`` keep working.
    await _impl.setup(bot)


__all__ = ["setup", "Alliance"]
"""Compatibility shim re-exporting `cogs.alliance`.

This keeps the original top-level import path `alliance` working while the
implementation lives in `cogs/alliance.py`.
"""

from cogs import alliance as _impl

try:
    Alliance = _impl.Alliance
except Exception:
    Alliance = None

async def setup(bot):
    """Delegate setup to the moved implementation."""
    await _impl.setup(bot)

__all__ = ["setup", "Alliance"]
