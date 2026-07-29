"""
oracle_keepalive.py — PM2 Watchdog for the Discord Bot
=======================================================
Runs as a separate PM2 process (oracle-keepalive).
Every CHECK_INTERVAL seconds it:
  1. Queries `pm2 jlist` to get the discordbot process state.
  2. If the process is stopped / errored / erroring → triggers pm2 restart.
  3. After a successful restart → resets the PM2 restart counter via `pm2 reset`.
  4. Logs all actions with timestamps.

Root cause this fixes:
  - The bot's asyncio loop gets blocked during heavy auto-redeem (80 guilds × HTTP),
    which starves the Discord WebSocket heartbeat.
  - Discord cuts the connection; the bot reconnects, counting as a PM2 "restart".
  - After 5 such reconnects PM2 hit max_restarts and permanently stopped the bot.
  - This watchdog detects the stopped state and revives it + resets the counter.
"""

import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone

# ── Configuration ─────────────────────────────────────────────────────────────
TARGET_PROCESS   = "discordbot"   # PM2 process name to watch
CHECK_INTERVAL   = 60             # seconds between health checks
RESTART_COOLDOWN = 90             # seconds to wait after a restart before checking again
MAX_CONSECUTIVE_RESTARTS = 5      # if we restart more than this many times in a row, back off
BACKOFF_SLEEP    = 600            # 10-minute back-off when restart keeps failing

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] keepalive: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("keepalive")


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a shell command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        log.warning(f"Command timed out: {' '.join(cmd)}")
        return -1, "", "timeout"
    except Exception as e:
        log.error(f"Command failed: {' '.join(cmd)} — {e}")
        return -1, "", str(e)


def get_process_status(name: str) -> dict | None:
    """Return the PM2 process dict for `name`, or None if not found."""
    rc, stdout, stderr = run(["pm2", "jlist"])
    if rc != 0:
        log.error(f"pm2 jlist failed: {stderr}")
        return None
    try:
        processes = json.loads(stdout)
        for proc in processes:
            if proc.get("name") == name:
                return proc
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse pm2 jlist: {e}")
    return None


def is_unhealthy(proc: dict) -> bool:
    """Return True if the process needs to be restarted."""
    status   = proc.get("pm2_env", {}).get("status", "unknown")
    pm_id    = proc.get("pm_id", "?")
    restarts = proc.get("pm2_env", {}).get("restart_time", 0)
    log.info(f"[{TARGET_PROCESS}] status={status}, restarts={restarts}, pm_id={pm_id}")
    return status in ("stopped", "errored", "erroring", "one-launch-status")


def restart_process(name: str) -> bool:
    """Restart the process. Return True if successful."""
    log.warning(f"⚠️  {name} is unhealthy — triggering restart...")
    rc, stdout, stderr = run(["pm2", "restart", name, "--update-env"])
    if rc == 0:
        log.info(f"✅ pm2 restart {name} succeeded.")
        return True
    else:
        log.error(f"❌ pm2 restart {name} failed (rc={rc}): {stderr}")
        return False


def reset_restart_counter(name: str) -> None:
    """Reset the PM2 restart counter so it doesn't hit max_restarts."""
    rc, _, stderr = run(["pm2", "reset", name])
    if rc == 0:
        log.info(f"🔄 pm2 reset {name} — restart counter cleared.")
    else:
        log.warning(f"pm2 reset {name} failed: {stderr}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    log.info(f"🚀 oracle_keepalive started — watching '{TARGET_PROCESS}' every {CHECK_INTERVAL}s")
    consecutive_restart_failures = 0

    while True:
        try:
            proc = get_process_status(TARGET_PROCESS)

            if proc is None:
                log.warning(f"Process '{TARGET_PROCESS}' not found in PM2 list — retrying in {CHECK_INTERVAL}s")
            elif is_unhealthy(proc):
                success = restart_process(TARGET_PROCESS)
                if success:
                    consecutive_restart_failures = 0
                    # Wait for the bot to fully boot before checking again
                    log.info(f"⏳ Waiting {RESTART_COOLDOWN}s for bot to stabilise...")
                    time.sleep(RESTART_COOLDOWN)
                    # Reset the counter AFTER it's back up so PM2 doesn't hit max_restarts again
                    reset_restart_counter(TARGET_PROCESS)
                    # Skip the normal sleep — check immediately after cooldown
                    continue
                else:
                    consecutive_restart_failures += 1
                    if consecutive_restart_failures >= MAX_CONSECUTIVE_RESTARTS:
                        log.error(
                            f"🚨 {MAX_CONSECUTIVE_RESTARTS} consecutive restart failures. "
                            f"Backing off for {BACKOFF_SLEEP}s before trying again."
                        )
                        time.sleep(BACKOFF_SLEEP)
                        consecutive_restart_failures = 0
                        continue
            else:
                # Process is healthy — also opportunistically reset counter if restarts are high
                restart_time = proc.get("pm2_env", {}).get("restart_time", 0)
                if restart_time >= 10:
                    log.info(f"🔄 Restart count is {restart_time} — proactively resetting counter.")
                    reset_restart_counter(TARGET_PROCESS)
                consecutive_restart_failures = 0

        except Exception as e:
            log.exception(f"Unexpected error in watchdog loop: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
