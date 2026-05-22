import os
from aiohttp import web
from datetime import datetime
import logging
import asyncio
import sys
import importlib.util

_LOGGER = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

# Track bot start time for uptime reporting
_START_TIME = datetime.utcnow()

async def start_health_server():
    """Start a lightweight HTTP server for health checks.

    Returns the port on success, or None if the server could not be started
    (for example, because the port is already in use).
    """
    port = int(os.environ.get('PORT', '8080'))
    app = web.Application()

    async def handle_root(request):
        """Root endpoint - simple OK response"""
        return web.Response(text='OK', content_type='text/plain')

    async def handle_health(request):
        """Health check endpoint - MUST respond quickly for Render.
        
        This endpoint is called by Render's health check system every 30 seconds.
        It MUST respond within 1-2 seconds or Render will send SIGTERM to kill the bot.
        
        We keep this endpoint ULTRA-FAST with NO blocking operations:
        - No MongoDB pings (moved to /status endpoint)
        - No external API calls
        - Just return uptime and timestamp
        
        This ensures Render always sees the bot as healthy.
        """
        uptime_seconds = (datetime.utcnow() - _START_TIME).total_seconds()
        
        resp = {
            'status': 'healthy',
            'uptime_seconds': int(uptime_seconds),
            'timestamp': datetime.utcnow().isoformat()
        }
        
        logger.debug(f"Health check: OK (uptime: {int(uptime_seconds)}s)")
        return web.json_response(resp)

    async def handle_status(request):
        """Detailed status endpoint with MongoDB diagnostics.
        
        This endpoint can take longer and includes detailed diagnostics.
        Use this for debugging, not for health checks.
        """
        uptime_seconds = (datetime.utcnow() - _START_TIME).total_seconds()
        
        resp = {
            'status': 'ok',
            'uptime_seconds': int(uptime_seconds),
            'time': datetime.utcnow().isoformat()
        }

        # Report whether pymongo is installed and its version
        try:
            import pymongo
            resp['pymongo_installed'] = True
            resp['pymongo_version'] = getattr(pymongo, '__version__', None)
        except Exception:
            resp['pymongo_installed'] = False
            resp['pymongo_version'] = None

        # Check if MONGO_URI is configured
        mongo_uri = os.environ.get('MONGO_URI')
        resp['mongo_uri_present'] = bool(mongo_uri)
        
        # Try to ping MongoDB (with timeout to avoid blocking)
        if mongo_uri and resp['pymongo_installed']:
            try:
                # Import the async wrapper
                from db.mongo_client_wrapper import get_mongo_client
                
                # Run with timeout to prevent hanging
                async def ping_with_timeout():
                    try:
                        client = await asyncio.wait_for(
                            get_mongo_client(mongo_uri, connect_timeout_ms=2000),
                            timeout=3.0
                        )
                        # Quick ping in thread pool
                        await asyncio.wait_for(
                            asyncio.to_thread(client.admin.command, 'ping'),
                            timeout=2.0
                        )
                        return {'ok': True}
                    except asyncio.TimeoutError:
                        return {'ok': False, 'error': 'Timeout'}
                    except Exception as e:
                        return {'ok': False, 'error': str(e)}
                
                resp['mongo_ping'] = await ping_with_timeout()
                
            except Exception as e:
                resp['mongo_ping'] = {'ok': False, 'error': str(e)}

        # Add diagnostic info about import resolution
        try:
            resp['sys_path'] = sys.path[:5]  # Only first 5 to keep response small
        except Exception:
            resp['sys_path'] = []

        def _spec_info(name: str):
            try:
                spec = importlib.util.find_spec(name)
                if not spec:
                    return None
                return {'name': name, 'origin': getattr(spec, 'origin', None)}
            except Exception as e:
                return {'error': str(e)}

        resp['spec_db'] = _spec_info('db')
        resp['spec_db_mongo_adapters'] = _spec_info('db.mongo_adapters')

        return web.json_response(resp)

    app.add_routes([
        web.get('/', handle_root),
        web.get('/health', handle_health),
        web.get('/status', handle_status),
    ])

    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        try:
            await site.start()
            logger.info(f'Health server started on port {port}')
            logger.info(f'  - Health check: http://0.0.0.0:{port}/health (fast)')
            logger.info(f'  - Status: http://0.0.0.0:{port}/status (detailed)')
        except OSError as e:
            # Port likely in use; log and return None so caller can continue
            logger.warning(f"Health server could not bind to 0.0.0.0:{port}: {e}")
            # Attempt to clean up runner
            try:
                await runner.cleanup()
            except Exception:
                pass
            return None
    except Exception as e:
        logger.exception(f"Failed to start health server: {e}")
        try:
            await runner.cleanup()
        except Exception:
            pass
        return None

    # Keep running until canceled; aiohttp site runs in background on the loop
    return port
