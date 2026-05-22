import os
import asyncio
import aiohttp
import logging

logger = logging.getLogger(__name__)

# Module-level state
_last_status_up = None


async def _check_once(session, url, timeout=10):
    try:
        async with session.get(url, timeout=timeout) as resp:
            status_ok = resp.status == 200
            text = await resp.text()
            return status_ok, resp.status, text
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return False, None, str(e)


async def start_uptime_checker(bot):
    """
    Background task that periodically pings a URL and optionally posts status changes to a Discord channel.

    Environment variables:
      UPTIME_URL - URL to ping (defaults to your service root or /health)
      UPTIME_INTERVAL - seconds between checks (default 300)
      UPTIME_POST_CHANNEL - optional channel ID to post status change messages
      UPTIME_POST_ON_CHANGE_ONLY - if '1' then post only when status changes; default '1'
    """
    global _last_status_up

    url = os.environ.get('UPTIME_URL') or f"http://localhost:{os.environ.get('PORT','8080')}/health"
    interval = int(os.environ.get('UPTIME_INTERVAL', '300'))
    post_channel = os.environ.get('UPTIME_POST_CHANNEL')
    post_on_change_only = os.environ.get('UPTIME_POST_ON_CHANGE_ONLY', '1') == '1'

    logger.info(f'Uptime checker will ping: {url} every {interval}s')

    await asyncio.sleep(5)  # small delay to allow server to warm up

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                is_up, status_code, body = await _check_once(session, url)
                now = asyncio.get_event_loop().time()

                if _last_status_up is None:
                    _last_status_up = is_up

                # Log details
                logger.info(f'Uptime check: url={url} up={is_up} status={status_code}')

                should_post = True
                if post_on_change_only and (_last_status_up is not None):
                    should_post = (is_up != _last_status_up)

                if should_post and post_channel:
                    try:
                        channel_id = int(post_channel)
                        channel = bot.get_channel(channel_id)
                        if channel is None:
                            # Try fetching the channel
                            channel = await bot.fetch_channel(channel_id)

                        if channel:
                            if is_up:
                                await channel.send(f'✅ Uptime check: {url} is UP (status={status_code})')
                            else:
                                await channel.send(f'❌ Uptime check: {url} is DOWN; error={body}')
                    except Exception as e:
                        logger.error(f'Failed to post uptime status to channel {post_channel}: {e}')

                _last_status_up = is_up

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f'Uptime checker iteration failed: {e}')

            await asyncio.sleep(interval)
