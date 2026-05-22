"""Compatibility shim re-exporting cogs.event commands.

This file exists to preserve existing imports while the real implementation
is moved into the `cogs/` package. Importing `setup_event_commands` from
this module will load the implementation from `cogs.events`.
"""

from cogs.events import setup_event_commands

__all__ = ["setup_event_commands"]