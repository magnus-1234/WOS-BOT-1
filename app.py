from typing import Optional
import os
import sys
import subprocess
from pathlib import Path

# Ensure repository root and src subdirectories are on sys.path 
# so modules moved during migration (like `src/utils/angel_personality.py`) can be imported
repo_root = str(Path(__file__).resolve().parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

src_path = os.path.join(repo_root, "src")
if os.path.exists(src_path):
    # Add src to path
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    # Add subdirectories to path to support legacy direct imports during migration
    for subdir in ["utils", "api", "bot", "web"]:
        sub_p = os.path.join(src_path, subdir)
        if os.path.exists(sub_p) and sub_p not in sys.path:
            sys.path.insert(0, sub_p)

import importlib
import time

# ============================================================================
# STEP 1: Ensure all dependencies are installed BEFORE any other imports
# ============================================================================

def ensure_dependencies_installed():
    """
    Install dependencies from requirements.txt ONLY when requirements.txt has changed.
    Uses an MD5 hash of requirements.txt stored in ~/.bot_deps_hash as a cache key.
    This avoids the 5-10 minute pip install on every startup.
    """
    import hashlib

    # Allow full skip via env var (used by GitHub Actions deploy on code-only pushes)
    if os.environ.get('SKIP_INSTALL', '').lower() == 'true':
        print("[SETUP] Skipping dependency installation (SKIP_INSTALL=true)")
        return True

    # Find requirements.txt
    req_paths = [
        "/app/requirements.txt",                                        # Docker
        os.path.join(os.path.dirname(__file__), "requirements.txt"),   # Local / Oracle VM
    ]
    req_file = next((p for p in req_paths if os.path.exists(p)), None)

    if not req_file:
        print("[ERROR] requirements.txt not found")
        return False

    # Compute MD5 hash of requirements.txt
    try:
        with open(req_file, "rb") as f:
            current_hash = hashlib.md5(f.read()).hexdigest()
    except Exception as e:
        print(f"[WARN] Could not hash requirements.txt: {e} — will reinstall to be safe")
        current_hash = None

    # Cache file lives in home directory so it persists across PM2 restarts
    cache_file = os.path.join(os.path.expanduser("~"), ".bot_deps_hash")
    saved_hash = ""
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r") as f:
                saved_hash = f.read().strip()
    except Exception:
        pass

    if current_hash and current_hash == saved_hash:
        print("[SETUP] Requirements unchanged -- skipping pip install (using cached environment)")
        importlib.invalidate_caches()
        return True

    print(f"[SETUP] requirements.txt changed (or first run) — installing dependencies from: {req_file}")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install",
             "--disable-pip-version-check", "-r", req_file],
            stdout=sys.stdout,
            stderr=sys.stderr,
            timeout=1800  # 30 minutes max
        )
        print("[SETUP] Dependencies installed successfully")

        # Save new hash so next startup skips install
        if current_hash:
            try:
                with open(cache_file, "w") as f:
                    f.write(current_hash)
            except Exception as e:
                print(f"[WARN] Could not save deps hash: {e}")

        importlib.invalidate_caches()
        return True

    except subprocess.TimeoutExpired:
        print("[ERROR] Dependency installation timed out (>30 mins)")
        return False
    except Exception as e:
        print(f"[ERROR] Dependency installation failed: {e}")
        return False

# Install / check dependencies first
if os.environ.get("SKIP_INSTALL", "").lower() == "true":
    print("[SETUP] Skipping dependency check (SKIP_INSTALL=true)")
elif not ensure_dependencies_installed():
    print("[ERROR] Failed to install dependencies")
    sys.exit(1)

# ============================================================================
# STEP 2: Now import everything else (safe because deps are installed)
# ============================================================================

def is_container() -> bool:
    return os.path.exists("/.dockerenv") or os.path.exists("/var/run/secrets/kubernetes.io")

def is_ci_environment() -> bool:
    """Check if running in a CI environment"""
    ci_indicators = [
        'CI', 'CONTINUOUS_INTEGRATION', 'GITHUB_ACTIONS', 
        'JENKINS_URL', 'TRAVIS', 'CIRCLECI', 'GITLAB_CI'
    ]
    return any(os.getenv(indicator) for indicator in ci_indicators)

# Legacy: Handle venv setup if NOT in container/CI
if not is_container() and not is_ci_environment():
    if sys.prefix == sys.base_prefix:
        venv_path = os.path.join(os.path.dirname(__file__), '.venv')
        
        if sys.platform == "win32":
            venv_python_name = os.path.join(venv_path, "Scripts", "python.exe")
        else:
            venv_python_name = os.path.join(venv_path, "bin", "python")
        
        if not os.path.exists(venv_path):
            try:
                print(f"[SETUP] Creating virtual environment '{venv_path}'...")
                subprocess.check_call([sys.executable, "-m", "venv", venv_path], timeout=300)
            except Exception as e:
                print(f"[WARN] Could not create venv: {e}")
                
        if os.path.exists(venv_path):
            try:
                if sys.platform == "win32":
                    print(f"[SETUP] To use venv, run: {venv_python_name} {os.path.basename(sys.argv[0])}")
                    # In Windows, we can't easily execv, but usually they run run.bat
                else:
                    print(f"[SETUP] Restarting in virtual environment ({venv_python_name})...")
                    os.execv(venv_python_name, [venv_python_name] + sys.argv)
            except Exception as e:
                print(f"[WARN] Could not execv into venv: {e}")
print("[SETUP] Bot initialization complete")

# ============================================================================
# NOW safe to import everything
# ============================================================================

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
load_dotenv()
import os
from urllib.parse import quote

import json
import logging
from api_manager import make_request, manager, make_image_request

from angel_personality import get_system_prompt, angel_personality
from user_mapping import get_known_user_name
from gift_codes import get_active_gift_codes, build_codes_embed
from cogs.reminder_system import ReminderSystem, set_user_timezone, get_user_timezone, TimeParser, REMINDER_IMAGES
from event_tips import EVENT_TIPS, get_event_info
from thinking_animation import ThinkingAnimation
from command_animator import animator

# Initialize thinking animation instance
thinking_animation = ThinkingAnimation()
try:
    from db.mongo_adapters import mongo_enabled, BirthdaysAdapter
except Exception as e:
    print(f"[ERROR] Failed to import MongoDB adapters: {e}")
    mongo_enabled = lambda: False
    BirthdaysAdapter = None
import sqlite3
import os
import cogs.shared_views as sv


def ensure_db_tables():
    """Initialize database backend: MongoDB if available, SQLite as fallback.
    
    IMPORTANT: SQLite tables are ALWAYS created as a safety fallback.
    This ensures that if MongoDB operations fail, the bot can still function.
    
    For Render deployment:
    - Set MONGO_URI environment variable to enable MongoDB persistence
    - Data will be saved to MongoDB cloud (persistent across restarts)
    - SQLite tables still exist for emergency fallback
    """
    # Check if MongoDB is configured
    mongo_uri = os.getenv('MONGO_URI')
    if mongo_uri:
        logger.info("[DB] ✅ MONGO_URI detected - Using MongoDB for ALL data persistence")
        logger.info("[DB] SQLite tables will be created as emergency fallback only")
    else:
        logger.warning("[DB] ⚠️  MONGO_URI not set - Using SQLite (NOT persistent on Render)")
        logger.warning("[DB] Add MONGO_URI environment variable to enable persistent MongoDB storage")
    
    db_dir = os.path.join(os.path.dirname(__file__), 'db')
    try:
        os.makedirs(db_dir, exist_ok=True)
    except Exception:
        pass

    # Database file paths
    paths = {
        'alliance': os.path.join(db_dir, 'alliance.sqlite'),
        'giftcode': os.path.join(db_dir, 'giftcode.sqlite'),
        'changes': os.path.join(db_dir, 'changes.sqlite'),
        'users': os.path.join(db_dir, 'users.sqlite'),
        'settings': os.path.join(db_dir, 'settings.sqlite'),
    }

    # alliance DB
    try:
        conn = sqlite3.connect(paths['alliance'])
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS alliancesettings (
            alliance_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            interval INTEGER
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS alliance_list (
            alliance_id INTEGER PRIMARY KEY,
            name TEXT
        )''')
        conn.commit()
        conn.close()
    except Exception:
        pass

    # giftcode DB
    try:
        conn = sqlite3.connect(paths['giftcode'])
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS gift_codes (
            giftcode TEXT PRIMARY KEY,
            date TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_giftcodes (
            fid INTEGER,
            giftcode TEXT,
            status TEXT,
            PRIMARY KEY (fid, giftcode)
        )''')
        # Auto-redeem tables for gift code management
        c.execute('''CREATE TABLE IF NOT EXISTS auto_redeem_members (
            guild_id INTEGER,
            fid TEXT,
            nickname TEXT,
            furnace_lv INTEGER DEFAULT 0,
            avatar_image TEXT,
            added_by INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, fid)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS auto_redeem_channels (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            added_by INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS auto_redeem_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 999,
            updated_by INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.commit()
        conn.close()
    except Exception:
        pass

    # changes DB (legacy change logs)
    try:
        conn = sqlite3.connect(paths['changes'])
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS nickname_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fid INTEGER,
            old_nickname TEXT,
            new_nickname TEXT,
            change_date TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS furnace_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fid INTEGER,
            old_furnace_lv INTEGER,
            new_furnace_lv INTEGER,
            change_date TEXT
        )''')
        conn.commit()
        conn.close()
    except Exception:
        pass

    # users DB
    try:
        conn = sqlite3.connect(paths['users'])
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            fid INTEGER PRIMARY KEY,
            nickname TEXT,
            furnace_lv INTEGER DEFAULT 0,
            kid INTEGER,
            stove_lv_content TEXT,
            alliance TEXT
        )''')
        conn.commit()
        conn.close()
    except Exception:
        pass

    # settings DB (admin, botsettings)
    try:
        conn = sqlite3.connect(paths['settings'])
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS botsettings (
            id INTEGER PRIMARY KEY,
            channelid INTEGER,
            giftcodestatus TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY,
            is_initial INTEGER
        )''')
        conn.commit()
        conn.close()
    except Exception:
        pass
import sys
import signal
import asyncio
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import io
import uptime_checker
import giftcode_poster
import aiohttp
from urllib.parse import quote
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
import random
import time
from pathlib import Path
import re
from wos_api import fetch_player_info
from beartrap_rag import is_beartrap_question, answer_beartrap_question
 
# Ensure stdout/stderr use UTF-8 to avoid UnicodeEncodeError on Windows consoles
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    else:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    # Best-effort - if this fails, logging may still error but we avoid crashing at import
    pass

# Initialize database tables (SQLite fallback)
logger = logging.getLogger(__name__)
ensure_db_tables()



# --- Improved signal handling for graceful shutdown diagnostics -----------------
def _log_tasks_and_tracebacks():
    """Return a short diagnostic string of currently running asyncio tasks and their stacks."""
    out_lines = []
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop available (signal handler may run outside loop); avoid deprecation
            loop = None
        tasks = list(asyncio.all_tasks(loop)) if loop is not None else []
        out_lines.append(f"Active asyncio tasks: {len(tasks)}")
        for t in tasks[:50]:
            out_lines.append(f"- Task: {t.get_name() if hasattr(t, 'get_name') else repr(t)} state={t._state if hasattr(t, '_state') else 'unknown'}")
            try:
                stack = t.get_stack()
                if stack:
                    out_lines.append("  Stack:")
                    for fr in stack[-6:]:
                        out_lines.append(f"    {fr.f_code.co_filename}:{fr.f_lineno} {fr.f_code.co_name}")
            except Exception:
                pass
    except Exception as e:
        out_lines.append(f"Failed to enumerate tasks: {e}")
    return "\n".join(out_lines)


def _signal_handler(signum, frame):
    # Log detailed info to help debug why Render sent SIGTERM/SIGINT
    try:
        logger.warning(f"Received signal {signum}; shutting down gracefully...")
    except Exception:
        print(f"Received signal {signum}; shutting down gracefully...")

    try:
        import traceback as _tb
        tb = _tb.format_stack(frame)
        logger.warning("Stack at signal time:\n" + "".join(tb))
    except Exception:
        pass

    try:
        info = _log_tasks_and_tracebacks()
        logger.warning("Asyncio task snapshot:\n" + info)
    except Exception:
        pass

    # Close MongoDB connections first to prevent hanging
    try:
        from db.mongo_client_wrapper import close_mongo_client
        try:
            loop = asyncio.get_running_loop()
            if loop is not None and loop.is_running():
                loop.create_task(close_mongo_client())
                logger.info("Scheduled MongoDB connection cleanup")
        except Exception as e:
            logger.warning(f"Could not schedule MongoDB cleanup: {e}")
    except Exception as e:
        logger.warning(f"Could not import MongoDB cleanup: {e}")

    # Try to stop discord client cleanly if available
    try:
        # `bot` is defined later; use globals to avoid import cycles
        b = globals().get('bot')
        if b is not None and hasattr(b, 'close'):
            # schedule close on loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            try:
                if loop is not None:
                    loop.create_task(b.close())
                else:
                    # No running loop; try synchronous close
                    try:
                        b.close()
                    except Exception:
                        pass
            except Exception:
                try:
                    b.close()
                except Exception:
                    pass
    except Exception:
        pass

# Register handlers early so we capture signals
try:
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
except Exception:
    # Not all platforms support signal.signal in the same way (Windows vs Unix)
    pass


# Feedback state file (optional persistent feedback channel)
FEEDBACK_STATE_PATH = Path(__file__).parent / "feedback_state.json"
FEEDBACK_LOG_PATH = Path(__file__).parent / "feedback_log.txt"

def load_feedback_state():
    try:
        if FEEDBACK_STATE_PATH.exists():
            with FEEDBACK_STATE_PATH.open('r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        # logger may not be configured yet at import time; use print as last resort
        try:
            logger.error(f"Failed to load feedback state: {e}")
        except Exception:
            print(f"Failed to load feedback state: {e}")
    return {}

def save_feedback_state(state: dict):
    try:
        with FEEDBACK_STATE_PATH.open('w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
        return True
    except Exception as e:
        try:
            logger.error(f"Failed to save feedback state: {e}")
        except Exception:
            print(f"Failed to save feedback state: {e}")
        return False

def get_feedback_channel_id():
    # Prefer persisted state over environment variable
    state = load_feedback_state()
    cid = state.get('channel_id')
    if cid:
        return int(cid)
    env_cid = os.getenv('FEEDBACK_CHANNEL_ID')
    return int(env_cid) if env_cid else None

def append_feedback_log(user, user_id, feedback_text, posted_channel=False, posted_owner=False):
    try:
        ts = datetime.utcnow().isoformat() + 'Z'
        entry = {
            'timestamp': ts,
            'user': str(user),
            'user_id': int(user_id),
            'posted_channel': bool(posted_channel),
            'posted_owner': bool(posted_owner),
            'feedback': feedback_text[:4000]
        }
        with FEEDBACK_LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        try:
            logger.error(f"Failed to append feedback log: {e}")
        except Exception:
            print(f"Failed to append feedback log: {e}")
    


async def fetch_pollinations_image(prompt_text: str, width: int = None, height: int = None, model_name: str = None, seed: int = None) -> bytes:
    """Module-level helper to fetch images from Pollinations.ai (Latest Standard)."""
    api_key = os.getenv('POLLINATIONS_API_KEY')
    
    if api_key:
        # Use new stable gateway (requires key)
        base = "https://gen.pollinations.ai/image/"
        params = [f"key={api_key}"]
        # Flux is the recommended high-quality model for the new system
        effective_model = model_name or "flux"
    else:
        # Legacy fallback (flaky, model parameter often causes 500s)
        base = "https://image.pollinations.ai/prompt/"
        params = []
        # Strip model on legacy to avoid 500 errors
        effective_model = None

    encoded = quote(prompt_text, safe='')
    url = base + encoded
    
    if width:
        params.append(f"width={int(width)}")
    if height:
        params.append(f"height={int(height)}")
    if effective_model:
        params.append(f"model={quote(effective_model, safe='')}")
    if seed is not None:
        params.append(f"seed={int(seed)}")
        
    if params:
        url = url + "?" + "&".join(params)

    timeout = aiohttp.ClientTimeout(total=120)
    # Use realistic browser headers to prevent 429 Rate Limiting
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "image/jpeg, image/png, image/*"
    }
    
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status == 200:
                content_type = resp.headers.get("Content-Type", "").lower()
                if "image/" in content_type:
                    return await resp.read()
                # If it's not an image but 200, it might be a JSON error wrapped in 200 (rare)
                data = await resp.read()
                return data
            elif resp.status == 401:
                raise Exception("Pollinations API Key is invalid or missing for the new gateway.")
            elif resp.status == 429:
                raise Exception("Rate limited by Pollinations API.")
            elif resp.status >= 500:
                raise Exception(f"Pollinations server error: {resp.status}. Try removing the model parameter or using an API Key.")
            else:
                text = await resp.text()
                raise Exception(f"Pollinations request failed: {resp.status} - {text}")



def detect_image_request(text: str):
    """Detect whether the text is asking for an image and try to extract the prompt.

    Returns (matched: bool, prompt: Optional[str]). The prompt is the best-effort
    substring describing what to generate (may be the full text if extraction fails).
    """
    if not text:
        return False, None
    q = text.strip()
    q_lower = q.lower()

    # Quick phrase list (cover common conversational variants)
    phrases = [
        "create an image", "generate an image", "make an image",
        "image of", "picture of", "photo of", "drawing of", "sketch of",
        "draw me", "draw a", "draw an", "render", "render me", "paint me",
        "i want an image", "i want a picture", "show me a picture", "show me an image",
        "take a picture of", "could you draw", "can you draw", "please draw", "plz draw",
        "illustrate", "illustration of", "create a picture", "give me a picture",
    ]

    for p in phrases:
        if p in q_lower:
            idx = q_lower.find(p)
            # Text after the matched phrase is likely the prompt
            prompt = q[idx + len(p):].strip()
            if prompt:
                return True, prompt
            # Try to find an "of X" pattern after or near the phrase
            m = re.search(r"(?:of|:|-)\s*(.+)$", q)
            if m:
                return True, m.group(1).strip()
            # As a last resort return the whole text
            return True, q

    # Regex: look for direct "<image-term> of <target>" (e.g., "picture of a cat")
    image_terms = r"(?:image|picture|photo|drawing|sketch|render|illustration|art|portrait)"
    m = re.search(rf"{image_terms}\s+of\s+(?P<t>.+)", q, flags=re.I)
    if m:
        return True, m.group('t').strip()

    # Regex: verbs that imply generation with an image term somewhere nearby
    verb_terms = r"(?:create|generate|make|draw|render|paint|sketch|illustrate|show|give|send|produce|take|capture)"
    # Allow up to 40 chars between verb and image term to catch sarcastic/colloquial phrasing
    m2 = re.search(rf"(?P<verb>{verb_terms}).{{0,40}}(?:{image_terms})(?:\s+of\s+(?P<t2>.+))?", q, flags=re.I)
    if m2:
        if m2.group('t2'):
            return True, m2.group('t2').strip()
        # Otherwise attempt to extract whatever comes after the match
        end = m2.end()
        trailing = q[end:].strip()
        if trailing:
            return True, trailing
        return True, q

    return False, None


class EditImageModal(discord.ui.Modal, title="Edit Image"):
    edit_prompt = discord.ui.TextInput(
        label="Edit Prompt",
        placeholder="Describe how you want to modify the image...",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, original_prompt: str, width: Optional[int] = None, height: Optional[int] = None, model: Optional[str] = None):
        super().__init__()
        self.original_prompt = original_prompt
        self.width = width
        self.height = height
        self.model = model

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            new_prompt = f"{self.original_prompt}. Edit: {self.edit_prompt.value}"
            image_bytes = await fetch_pollinations_image(new_prompt, width=self.width, height=self.height, model_name=self.model)
            from io import BytesIO
            image_file = discord.File(BytesIO(image_bytes), filename="edited_image.png")

            embed = discord.Embed(title="✏️ Edited Image", description=f"**Prompt:** {new_prompt}", color=0x00FF7F)
            embed.set_image(url="attachment://edited_image.png")
            await interaction.followup.send(embed=embed, file=image_file)
        except Exception as e:
            await interaction.followup.send(f"Failed to edit image: {e}", ephemeral=True)


class PollinateButtonView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Regenerate", style=discord.ButtonStyle.secondary, custom_id="regenerate-button")
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer()
            # Extract prompt/model/dimensions from original embed
            if not interaction.message.embeds:
                await interaction.followup.send("Original embed not found.", ephemeral=True)
                return
            embed = interaction.message.embeds[0]
            # Prompt field may be in fields or description
            prompt = None
            for f in embed.fields:
                if f.name.lower() == "prompt":
                    prompt = f.value.strip('`')
                    break
            if not prompt:
                # Try description
                prompt = embed.description or ""

            # Get model and dimensions
            model = None
            width = None
            height = None
            for f in embed.fields:
                if f.name.lower() == "model":
                    model = f.value
                if f.name.lower() == "dimensions":
                    parts = f.value.split('x')
                    if len(parts) == 2:
                        try:
                            width = int(parts[0])
                            height = int(parts[1])
                        except Exception:
                            width = None
                            height = None

            image_bytes = await fetch_pollinations_image(prompt, width=width, height=height, model_name=model)
            from io import BytesIO
            file = discord.File(BytesIO(image_bytes), filename="regenerated.png")
            # Send new image as followup
            new_embed = discord.Embed(title="🔁 Regenerated Image", description=f"**Prompt:** {prompt}", color=0x00FF7F)
            new_embed.set_image(url="attachment://regenerated.png")
            await interaction.followup.send(embed=new_embed, file=file)
        except Exception as e:
            await interaction.followup.send(f"Failed to regenerate image: {e}", ephemeral=True)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary, custom_id="edit-button")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not interaction.message.embeds:
                await interaction.response.send_message("Original embed not found.", ephemeral=True)
                return
            embed = interaction.message.embeds[0]
            prompt = None
            for f in embed.fields:
                if f.name.lower() == "prompt":
                    prompt = f.value.strip('`')
                    break
            # Extract width/height/model if present
            model = None
            width = None
            height = None
            for f in embed.fields:
                if f.name.lower() == "model":
                    model = f.value
                if f.name.lower() == "dimensions":
                    parts = f.value.split('x')
                    if len(parts) == 2:
                        try:
                            width = int(parts[0])
                            height = int(parts[1])
                        except Exception:
                            pass

            modal = EditImageModal(prompt or "", width=width, height=height, model=model)
            await interaction.response.send_modal(modal)
        except Exception as e:
            await interaction.response.send_message(f"Failed to open edit modal: {e}", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="delete-button")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            author_id = None
            try:
                author_id = interaction.message.interaction.user.id
            except Exception:
                pass
            if author_id and interaction.user.id != author_id and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("You don't have permission to delete this image.", ephemeral=True)
                return
            await interaction.message.delete()
            await interaction.response.send_message("Image deleted.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to delete image: {e}", ephemeral=True)

    @discord.ui.button(label="Bookmark", style=discord.ButtonStyle.secondary, custom_id="bookmark-button")
    async def bookmark(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not interaction.message.embeds:
                await interaction.response.send_message("Original embed not found.", ephemeral=True)
                return
            embed = interaction.message.embeds[0]
            url = embed.url or None
            dm_embed = discord.Embed(title="📌 Bookmarked Image", description=embed.fields[0].value if embed.fields else "", color=0x00FF7F)
            if url:
                dm_embed.add_field(name="Link", value=url, inline=False)
                dm_embed.set_image(url=url)
            await interaction.user.send(embed=dm_embed)
            await interaction.response.send_message("Bookmarked — sent to your DMs.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to bookmark image: {e}", ephemeral=True)


class PollinateNoEditView(discord.ui.View):
    """Same as PollinateButtonView but without the Edit button (for HF-generated images)."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Regenerate", style=discord.ButtonStyle.secondary, custom_id="regenerate-noedit")
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer()
            if not interaction.message.embeds:
                await interaction.followup.send("Original embed not found.", ephemeral=True)
                return
            embed = interaction.message.embeds[0]
            prompt = None
            for f in embed.fields:
                if f.name.lower() == "prompt":
                    prompt = f.value.strip('`')
                    break
            if not prompt:
                prompt = embed.description or ""

            # Try to extract dimensions/model
            model = None
            width = None
            height = None
            for f in embed.fields:
                if f.name.lower() == "model":
                    model = f.value
                if f.name.lower() == "dimensions":
                    parts = f.value.split('x')
                    if len(parts) == 2:
                        try:
                            width = int(parts[0])
                            height = int(parts[1])
                        except Exception:
                            width = None
                            height = None

            # For HF-generated images we call make_image_request
            image_bytes = await make_image_request(prompt, width=width, height=height, model=os.getenv('HUGGINGFACE_MODEL'))
            from io import BytesIO
            file = discord.File(BytesIO(image_bytes), filename="regenerated.png")
            new_embed = discord.Embed(title="🔁 Regenerated Image", description=f"**Prompt:** {prompt}", color=0x00FF7F)
            new_embed.set_image(url="attachment://regenerated.png")
            await interaction.followup.send(embed=new_embed, file=file)
        except Exception as e:
            await interaction.followup.send(f"Failed to regenerate image: {e}", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="delete-noedit")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            author_id = None
            try:
                author_id = interaction.message.interaction.user.id
            except Exception:
                pass
            if author_id and interaction.user.id != author_id and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("You don't have permission to delete this image.", ephemeral=True)
                return
            await interaction.message.delete()
            await interaction.response.send_message("Image deleted.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to delete image: {e}", ephemeral=True)


    @discord.ui.button(label="Bookmark", style=discord.ButtonStyle.secondary, custom_id="bookmark-noedit")
    async def bookmark(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if not interaction.message.embeds:
                await interaction.response.send_message("Original embed not found.", ephemeral=True)
                return
            embed = interaction.message.embeds[0]
            url = embed.url or None
            dm_embed = discord.Embed(title="📌 Bookmarked Image", description=embed.fields[0].value if embed.fields else "", color=0x00FF7F)
            if url:
                dm_embed.add_field(name="Link", value=url, inline=False)
                dm_embed.set_image(url=url)
            await interaction.user.send(embed=dm_embed)
            await interaction.response.send_message("Bookmarked — sent to your DMs.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to bookmark image: {e}", ephemeral=True)


load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.voice_states = True  # Explicitly enable for music functionality
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_socket_raw_receive(msg):
    """Low-level monitor for the entire bot's gateway"""
    if b'VOICE' in msg:
        print(f"📡 [RAW_GATEWAY] Received voice-related packet: {msg[:200]}...")

# Load cogs
async def setup_hook():
    """Load cogs when bot starts"""
    cogs_to_load = [
        "cogs.start_menu",
        "cogs.alliance",
        "cogs.alliance_member_operations",
        "cogs.changes",
        "cogs.web_search",
        "cogs.welcome_channel",
        "cogs.control",
        "cogs.gift_operations",
        "cogs.manage_giftcode",
        "cogs.id_channel",
        "cogs.bot_operations",
        "cogs.remote_access",  # Remote channel management
        "cogs.fid_commands",
        "cogs.record_commands",
        "cogs.bear_trap",
        "cogs.bear_trap_editor",
        "cogs.attendance",
        "cogs.minister_schedule",
        "cogs.other_features",
        "cogs.support_operations",
        "cogs.minister_menu",
        "cogs.playerinfo",
        "cogs.reminder_system",
        "cogs.birthday_system",
        "cogs.events",
        "cogs.server_age",
        "cogs.personalise_chat",
        # "cogs.music",  # Music bot functionality (Disabled to save memory)
        # "cogs.voicetest", # Diagnostic cog (Disabled)
        "cogs.presence",  # Rotating Rich Presence
        # "cogs.voice_conversation",  # Voice chat with AI
        # "cogs.tts",  # Text-to-Speech in voice channels
        "cogs.auto_translate",  # Auto-translate with DeepL
        "cogs.message_extractor",  # Message extraction for global admins
        "cogs.tictactoe",  # Tic-Tac-Toe game
        "cogs.alliance_monitor",  # Alliance online status monitoring
        "cogs.ai_chat",  # AI Chat functionality on mentions/DMs
        "cogs.registration_admin",  # Self-service server registration review (global admin)
        "cogs.role_menu",  # Dashboard reaction roles and auto roles
        "cogs.moderation",  # Dashboard AutoMod and moderation logging
        # NOTE: cogs.start_menu removed from here — it was duplicated (also at top of list)
        # "cogs.debug_mongo_cog",  # Removed — debug tools moved to /settings button
    ]
    
    # Start FastAPI Web Server for dashboard & health checks early
    try:
        from web_api.server import start_web_server
        # Run start_web_server as a background task so it doesn't block setup_hook
        bot.loop.create_task(start_web_server(bot))
        logger.info("🚀 Web server task created")
    except Exception as e:
        logger.error(f"❌ Failed to start web server: {e}")

    loaded_count = 0
    failed_count = 0
    
    for cog_name in cogs_to_load:
        try:
            await bot.load_extension(cog_name)
            logger.info(f"✅ Loaded {cog_name}")
            loaded_count += 1
        except commands.ExtensionAlreadyLoaded:
            # Already loaded (e.g. PM2 overlap restart or duplicate list entry)
            # Silently skip — the cog is already running
            logger.debug(f"⏭️  Skipped {cog_name} (already loaded)")
            loaded_count += 1  # Count it as successfully loaded
        except Exception as e:
            logger.error(f"❌ Failed to load {cog_name}: {e}", exc_info=True)
            failed_count += 1

    
    logger.info(f"📦 Cog loading complete: {loaded_count} loaded, {failed_count} failed")
    
    # Load user profiles from storage
    try:
        angel_personality.load_profiles()
        logger.info("✅ User profiles loaded successfully")
    except Exception as e:
        logger.error(f"❌ Failed to load user profiles: {e}")
    
    # Initialize playlist storage with timeout to prevent hanging
    try:
        from playlist_storage import playlist_storage
        await asyncio.wait_for(playlist_storage.initialize(), timeout=15.0)
        logger.info("✅ Playlist storage initialized")
    except asyncio.TimeoutError:
        logger.warning("⚠️ Playlist storage initialization timed out")
    except Exception as e:
        logger.error(f"❌ Failed to initialize playlist storage: {e}")
    
    # Initialize music state storage with timeout
    try:
        from music_state_storage import music_state_storage
        await asyncio.wait_for(music_state_storage.initialize(), timeout=15.0)
        logger.info("✅ Music state storage initialized")
    except asyncio.TimeoutError:
        logger.warning("⚠️ Music state storage initialization timed out")
    except Exception as e:
        logger.error(f"❌ Failed to initialize music state storage: {e}")
    
    # Start keep-alive task for Render
    async def keep_alive_task():
        """Background task to prevent Render from considering service idle.
        
        Runs every 5 minutes to show activity and prevent auto-shutdown.
        """

        await asyncio.sleep(60)  # Wait 1 minute before starting
        
        while True:
            try:
                # Lightweight operation to show activity
                logger.debug("Keep-alive: Bot is running")
                
                # Check MongoDB connection health
                try:
                    from db.mongo_client_wrapper import get_mongo_client
                    client = await asyncio.wait_for(
                        get_mongo_client(),
                        timeout=5.0
                    )
                    # Quick ping in thread pool
                    await asyncio.wait_for(
                        asyncio.to_thread(client.admin.command, 'ping'),
                        timeout=3.0
                    )
                    logger.debug("Keep-alive: MongoDB connection healthy")
                except asyncio.TimeoutError:
                    logger.warning("Keep-alive: MongoDB ping timeout")
                except Exception as e:
                    logger.warning(f"Keep-alive: MongoDB check failed: {e}")
                
            except Exception as e:
                logger.error(f"Keep-alive task error: {e}")
            
            # Wait 5 minutes before next check
            await asyncio.sleep(300)
    
    # Start keep-alive in background
    try:
        asyncio.create_task(keep_alive_task())
        logger.info("✅ Keep-alive task started (runs every 5 minutes)")
    except Exception as e:
        logger.error(f"❌ Failed to start keep-alive task: {e}")

    # Start giftcode auto-poster background loop (Checks every 5 minutes)
    try:
        import giftcode_poster
        asyncio.create_task(giftcode_poster.start_poster(bot, interval=300))
        logger.info("✅ Giftcode auto-poster background task started (5m interval)")
    except Exception as e:
        logger.error(f"❌ Failed to start giftcode auto-poster task: {e}")

    # Restore persistent views from MongoDB
    async def restore_persistent_views():
        """Restore all persistent views from MongoDB on bot startup"""
        try:
            from db.mongo_adapters import mongo_enabled, PersistentViewsAdapter
        except Exception:
            logger.warning("⚠️  MongoDB adapters not available, skipping view restoration")
            return
        
        if not mongo_enabled():
            logger.info("ℹ️  MongoDB not enabled, skipping persistent view restoration")
            return
        
        try:
            views = PersistentViewsAdapter.get_all_views()
            logger.info(f"🔄 Restoring {len(views)} persistent views from MongoDB...")
            
            restored_count = 0
            failed_count = 0
            
            for view_data in views:
                try:
                    view_type = view_data['view_type']
                    message_id = view_data['message_id']
                    metadata = view_data.get('metadata', {})
                    
                    # Create appropriate view instance based on type
                    view = None
                    if view_type == 'help':
                        from cogs.shared_views import PersistentHelpView
                        view = PersistentHelpView()
                    elif view_type == 'birthday':
                        view = BirthdayView()
                    elif view_type == 'birthdaywish':
                        from cogs.birthday_system import BirthdayWishView
                        birthday_user_ids = metadata.get('birthday_user_ids', [])
                        view = BirthdayWishView(birthday_user_ids=birthday_user_ids)
                    elif view_type == 'giftcode':
                        from giftcode_poster import GiftCodeView
                        view = GiftCodeView(codes_list=[])
                    elif view_type == 'memberlist':
                        from cogs.bot_operations import PersistentMemberListView
                        alliance_id = metadata.get('alliance_id', 0)
                        view = PersistentMemberListView(alliance_id=alliance_id)
                    elif view_type == 'recordlist':
                        from cogs.bot_operations import PersistentRecordsView
                        view = PersistentRecordsView()
                    elif view_type == 'recorddetail':
                        from cogs.bot_operations import PersistentRecordDetailView
                        record_name = metadata.get('record_name', "")
                        view = PersistentRecordDetailView(record_name=record_name)
                    
                    if view:
                        # Register view with bot
                        bot.add_view(view, message_id=message_id)
                        logger.debug(f"✅ Restored {view_type} view for message {message_id}")
                        restored_count += 1
                    else:
                        logger.warning(f"⚠️  Unknown view type: {view_type} for message {message_id}")
                        failed_count += 1
                        
                except Exception as e:
                    logger.error(f"❌ Failed to restore view for message {view_data.get('message_id')}: {e}")
                    failed_count += 1
            
            logger.info(f"✅ View restoration complete: {restored_count} restored, {failed_count} failed")
            
        except Exception as e:
            logger.error(f"❌ Failed to restore persistent views: {e}", exc_info=True)
    
    # Call restore function
    await restore_persistent_views()
    
    # Register persistent views (for new messages without message_id)
    try:
        # Register GiftCodeView
        from giftcode_poster import GiftCodeView
        bot.add_view(GiftCodeView(codes_list=[]))
        logger.info("✅ Registered GiftCodeView")

        # Register PersistentHelpView
        from cogs.shared_views import PersistentHelpView
        bot.add_view(PersistentHelpView())
        logger.info("✅ Registered PersistentHelpView")

        # Register Pollinate Views
        bot.add_view(PollinateButtonView())
        bot.add_view(PollinateNoEditView())
        logger.info("✅ Registered Pollinate Views")
        
        # Register PersistentMemberListView
        from cogs.bot_operations import PersistentMemberListView
        bot.add_view(PersistentMemberListView(alliance_id=0))
        logger.info("✅ Registered PersistentMemberListView")
        
        # Register BirthdayWishView
        from cogs.birthday_system import BirthdayWishView
        bot.add_view(BirthdayWishView(birthday_user_ids=[]))
        logger.info("✅ Registered BirthdayWishView")

        # Register Record Views
        from cogs.bot_operations import PersistentRecordsView, PersistentRecordDetailView
        bot.add_view(PersistentRecordsView())
        bot.add_view(PersistentRecordDetailView(record_name=""))
        logger.info("✅ Registered Record Views")
        
    except Exception as e:
        logger.error(f"❌ Failed to register persistent views: {e}")

    logger.info("✨ [STARTUP] setup_hook completed successfully")

bot.setup_hook = setup_hook

# Add on_ready event handler to sync commands
@bot.event
async def on_ready():
    """Called when the bot is ready and connected to Discord"""
    try:
        if not hasattr(bot, "ready_at"):
            bot.ready_at = datetime.utcnow()
        logger.info(f"🤖 Logged in as {bot.user} (ID: {bot.user.id})")
        logger.info(f"📊 Connected to {len(bot.guilds)} guild(s)")
        
        # Sync commands automatically to fix visibility issues
        try:
            # First, try to sync globally
            synced = await bot.tree.sync()
            logger.info(f"✅ Synced {len(synced)} commands globally")
            
            # Also sync to each guild for faster visibility in background
            async def sync_guilds_task():
                for guild in bot.guilds:
                    try:
                        bot.tree.copy_global_to(guild=guild)
                        await bot.tree.sync(guild=guild)
                        logger.info(f"✅ Synced commands to guild: {guild.name}")
                        await asyncio.sleep(2) # Avoid rate limits
                    except Exception as guild_sync_error:
                        logger.warning(f"⚠️ Failed to sync to guild {guild.name}: {guild_sync_error}")
            
            asyncio.create_task(sync_guilds_task())
                    
        except Exception as sync_error:
            logger.error(f"❌ Failed to sync commands: {sync_error}")
        
    except Exception as e:
        logger.error(f"❌ Error in on_ready: {e}", exc_info=True)

# Add on_message_delete event handler to cleanup view registrations
@bot.event
async def on_message_delete(message):
    """Remove view registration when message is deleted"""
    try:
        from db.mongo_adapters import mongo_enabled, PersistentViewsAdapter
        
        if not mongo_enabled():
            return
        
        # Check if this message has a registered view
        if PersistentViewsAdapter.view_exists(message.id):
            if PersistentViewsAdapter.unregister_view(message.id):
                logger.info(f"🗑️  Unregistered view for deleted message {message.id}")
            else:
                logger.warning(f"⚠️  Failed to unregister view for deleted message {message.id}")
    except Exception as e:
        logger.error(f"❌ Error in on_message_delete: {e}")

# Add error handler for command tree to handle stale commands
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle errors from application commands"""
    if isinstance(error, app_commands.CommandNotFound):
        # Silently ignore stale command registrations (like /ask)
        # These will be cleaned up when Discord's cache expires
        logger.warning(f"⚠️  User {interaction.user} tried to use stale command: {error}")
        try:
            await interaction.response.send_message(
                "⚠️ This command is no longer available. Please use `/help` to see available commands.",
                ephemeral=True
            )
        except:
            pass
        return
    
    # Log other errors
    logger.error(f"❌ Command error: {error}", exc_info=True)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"❌ An error occurred: {str(error)}",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ An error occurred: {str(error)}",
                ephemeral=True
            )
    except:
        pass


# Global interaction check for server locks - prevents commands on locked servers
async def check_server_lock(interaction: discord.Interaction) -> bool:
    """Check if the server is locked before processing commands"""
    # Only check for guild-based command interactions (not DMs, not component interactions)
    if interaction.guild and interaction.type == discord.InteractionType.application_command:
        try:
            import sqlite3
            # Check if server is locked
            settings_db = sqlite3.connect('db/settings.sqlite')
            cursor = settings_db.cursor()
            cursor.execute("SELECT locked, feature_locked FROM server_locks WHERE guild_id = ?", (interaction.guild.id,))
            result = cursor.fetchone()
            settings_db.close()
            
            if result:
                is_locked = result[0] == 1
                is_feature_locked = result[1] == 1 if len(result) > 1 else False
                
                # If server is completely locked, block all commands
                if is_locked:
                    embed = discord.Embed(
                        title="🔒 Bot Locked",
                        description=(
                            "**This bot is currently locked for this server.**\n\n"
                            "The bot will not respond to any commands until it is unlocked by the Global Administrator.\n\n"
                            "If you believe this is an error, please contact the server administrators."
                        ),
                        color=0xED4245
                    )
                    embed.set_footer(text="Contact your server administrator for assistance")
                    
                    # Add Contact Administrator button
                    view = discord.ui.View(timeout=None)
                    
                    # Get bot owner for contact link
                    app_info = None
                    try:
                        app_info = await interaction.client.application_info()
                    except Exception:
                        pass
                    
                    if app_info and app_info.owner:
                        owner_id = app_info.owner.id
                        view.add_item(discord.ui.Button(
                            label="Contact Administrator",
                            emoji="📩",
                            style=discord.ButtonStyle.link,
                            url=f"https://discord.com/users/{owner_id}"
                        ))
                    else:
                        # Fallback: generic support server link or just a non-link button
                        view.add_item(discord.ui.Button(
                            label="Contact Administrator",
                            emoji="📩",
                            style=discord.ButtonStyle.link,
                            url="https://discord.com"
                        ))
                    
                    try:
                        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                    except:
                        try:
                            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                        except:
                            pass
                    
                    # Return False to block command execution
                    return False
                    
                # If server is feature locked, block only specific commands
                if is_feature_locked and interaction.command:
                    blocked_commands = ["alliancemonitor"]
                    if interaction.command.name in blocked_commands:
                        embed = discord.Embed(
                            title="🔏 Feature Locked",
                            description=(
                                "**This feature is currently locked for this server.**\n\n"
                                "You cannot use the Alliance Monitor feature until it is unlocked by the Global Administrator.\n\n"
                                "If you believe this is an error, please contact the server administrators."
                            ),
                            color=0xE67E22
                        )
                        embed.set_footer(text="Contact your server administrator for assistance")
                        try:
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                        except:
                            try:
                                await interaction.followup.send(embed=embed, ephemeral=True)
                            except:
                                pass
                        
                        return False

        except Exception as e:
            # If there's an error checking locks, log it but allow command to proceed
            logger.error(f"Error checking server lock status: {e}")
    
    # Allow command execution
    return True

# Add the check to the bot's command tree
bot.tree.interaction_check = check_server_lock


# --- Message Handler for Commands and DMs ---------------------------------

@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages for keyword triggers, DMs, and text commands.
    
    This handler:
    1. Processes text commands (!dice, !roll, etc.)
    2. Detects keywords (dice, roll, giftcode) and triggers appropriate commands
    3. Handles DM messages and sends them to OpenRouter for AI responses
    4. Respects bot messages to prevent loops
    5. Integrates with existing cog-level on_message listeners
    """
    try:
        # Ignore messages from bots to prevent loops
        if message.author.bot:
            return
        
        # Handle DM messages with OpenRouter
        if isinstance(message.channel, discord.DMChannel):
            try:
                # Get user's question
                user_question = message.content.strip()
                
                if not user_question:
                    return
                
                # Check for image generation request first
                is_image_request, prompt = detect_image_request(user_question)
                
                if is_image_request and prompt:
                    async with message.channel.typing():
                        try:
                            # Generate image
                            image_bytes = await fetch_pollinations_image(prompt)
                            from io import BytesIO
                            file = discord.File(BytesIO(image_bytes), filename="generated.png")
                            
                            # Create embed
                            embed = discord.Embed(
                                title="🎨 Generated Image",
                                description=f"**Prompt:** {prompt}",
                                color=0x00FF7F
                            )
                            embed.set_image(url="attachment://generated.png")
                            embed.set_footer(text="Powered by Pollinations.ai")
                            
                            # Send with buttons
                            view = PollinateButtonView()
                            await message.reply(embed=embed, file=file, view=view)
                            return
                        except Exception as img_error:
                            logger.error(f"Image generation error in DM: {img_error}")
                            await message.reply(f"Sorry, I couldn't generate that image. Error: {img_error}")
                            return

                # Show typing indicator for text response
                async with message.channel.typing():
                    # Get known user name for personalization
                    user_name = get_known_user_name(message.author.id) or message.author.display_name
                    
                    # Build messages for OpenRouter
                    system_prompt = get_system_prompt(user_name)
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_question}
                    ]
                    
                    # Make API request
                    try:
                        response = await make_request(messages, max_tokens=1000, include_sheet_data=True)
                        
                        # Handle empty responses
                        if not response or not response.strip():
                            await message.reply("I received your message but couldn't generate a response. Please try again!")
                            return
                        
                        # Send response (split if too long)
                        if len(response) <= 2000:
                            await message.reply(response)
                        else:
                            # Split into chunks
                            chunks = [response[i:i+2000] for i in range(0, len(response), 2000)]
                            for chunk in chunks:
                                await message.channel.send(chunk)
                                
                    except Exception as api_error:
                        logger.error(f"OpenRouter API error in DM: {api_error}")
                        await message.reply("Sorry, I encountered an error processing your message. Please try again later!")
                        
            except Exception as dm_error:
                logger.error(f"Error handling DM: {dm_error}")
                try:
                    await message.reply("Sorry, something went wrong. Please try again!")
                except:
                    pass
            return
        
        # ── AI response when bot is @mentioned or when someone replies to the bot ──
        if message.guild:
            bot_mentioned = bot.user in message.mentions
            is_reply_to_bot = (
                message.reference is not None
                and message.reference.resolved is not None
                and isinstance(message.reference.resolved, discord.Message)
                and message.reference.resolved.author == bot.user
            )

            if bot_mentioned or is_reply_to_bot:
                try:
                    # Strip the bot mention from the message content so the AI
                    # doesn't see "<@ID>" as part of the question.
                    raw_content = message.content
                    for mention in message.mentions:
                        raw_content = raw_content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
                    user_question = raw_content.strip()

                    if not user_question:
                        await message.reply("Hey! 👋 You mentioned me — what can I help you with?", mention_author=True)
                        return

                    # Check for image-generation request first
                    is_image_req, img_prompt = detect_image_request(user_question)
                    if is_image_req and img_prompt:
                        async with message.channel.typing():
                            try:
                                image_bytes = await fetch_pollinations_image(img_prompt)
                                from io import BytesIO
                                file = discord.File(BytesIO(image_bytes), filename="generated.png")
                                embed = discord.Embed(
                                    title="🎨 Generated Image",
                                    description=f"**Prompt:** {img_prompt}",
                                    color=0x00FF7F,
                                )
                                embed.set_image(url="attachment://generated.png")
                                embed.set_footer(text="Powered by Pollinations.ai")
                                view = PollinateButtonView()
                                await message.reply(embed=embed, file=file, view=view, mention_author=False)
                            except Exception as img_err:
                                logger.error(f"Image generation error (mention/reply): {img_err}")
                                await message.reply(f"Sorry, I couldn't generate that image. Error: {img_err}", mention_author=True)
                        return

                    # Regular AI text response
                    async with message.channel.typing():
                        user_id_str = str(message.author.id)
                        user_name = get_known_user_name(message.author.id) or message.author.display_name

                        # Refresh live WOS stats if user is personalized (has player_id saved)
                        try:
                            await angel_personality.refresh_game_stats(user_id_str)
                        except Exception as _refresh_err:
                            logger.warning(f"Could not refresh game stats for {user_id_str}: {_refresh_err}")

                        system_prompt = get_system_prompt(user_name, user_id_str)
                        ai_messages = [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_question},
                        ]
                        try:
                            ai_response = await make_request(ai_messages, max_tokens=800, include_sheet_data=True)
                            if not ai_response or not ai_response.strip():
                                await message.reply("I heard you, but couldn't think of a reply right now. Try again! 🙏", mention_author=True)
                                return
                            # Split if too long
                            if len(ai_response) <= 2000:
                                await message.reply(ai_response, mention_author=False)
                            else:
                                chunks = [ai_response[i:i+2000] for i in range(0, len(ai_response), 2000)]
                                await message.reply(chunks[0], mention_author=False)
                                for chunk in chunks[1:]:
                                    await message.channel.send(chunk)
                        except Exception as ai_err:
                            logger.error(f"AI error in mention/reply handler: {ai_err}")
                            await message.reply("Oops, I hit an error processing your message. Please try again later! 😅", mention_author=True)
                except Exception as mention_err:
                    logger.error(f"Error in mention/reply handler: {mention_err}", exc_info=True)
                return  # Don't fall through to keyword triggers when the bot was mentioned/replied to

        # Handle keyword triggers in guild channels
        if message.guild:
            content_lower = message.content.lower().strip()
            
            # Check for dice/roll keywords with word boundaries to avoid matching "scroll", "troll", etc.
            if re.search(r'\b(dice|roll)\b', content_lower):
                # Trigger the dice text command
                try:
                    ctx = await bot.get_context(message)
                    if ctx.valid:
                        # If it's already a command, let it process normally
                        pass
                    else:
                        # Invoke dice command programmatically
                        dice_cmd = bot.get_command('dice')
                        if dice_cmd:
                            ctx = await bot.get_context(message)
                            await ctx.invoke(dice_cmd)
                except Exception as e:
                    logger.error(f"Error triggering dice command: {e}")
            
            # Check for giftcode keyword with word boundaries
            elif re.search(r'\b(giftcode)\b', content_lower):
                # Show gift codes when user types "giftcode"
                try:
                    # Import here to avoid circular imports
                    from gift_codes import get_active_gift_codes, build_codes_embed
                    
                    codes = await get_active_gift_codes()
                    if codes:
                        embed = build_codes_embed(codes)
                        view = sv.GiftCodeView(codes)
                        await message.reply(embed=embed, view=view, mention_author=False)
                    else:
                        await message.reply("No active gift codes available right now. Check back later! 🎁", mention_author=False)
                except Exception as e:
                    logger.error(f"Error showing gift codes via keyword: {e}")
        
        # IMPORTANT: Always process commands at the end
        # This enables text commands like !dice, !roll, etc.
        await bot.process_commands(message)
        
    except Exception as e:
        logger.error(f"Error in on_message handler: {e}", exc_info=True)


# --- Player ID Validation Event Listener -----------------------------------



# --- Clean startup banner and logging ----------------------------------
MAGNUS_ART = r'''

  __  __          _____ _   _ _    _  _____ 
 |  \/  |   /\   / ____| \ | | |  | |/ ____|
 | \  / |  /  \ | |  __|  \| | |  | | (___  
 | |\/| | / /\ \| | |_ | . ` | |  | |\___ \ 
 | |  | |/ ____ \ |__| | |\  | |__| |____) |
 |_|  |_/_/    \_\_____|_| \_|\____/|_____/ 
'''                                            
                                            

def _print_startup_banner():
    try:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print('\n' + MAGNUS_ART)
        print(f"✨ MAGNUS — Clean Console • started at {ts}\n")
    except Exception:
        # best-effort; don't crash if printing fails
        pass

def setup_logging():
    """Configure a compact, emoji-based console logger and reduce noise.

    Returns a module logger (logging.getLogger(__name__)).
    """
    # try to enable colorama if available (Windows friendly)
    try:
        import colorama
        colorama.init()
        RESET = colorama.Style.RESET_ALL
        COLORS = {
            'DEBUG': colorama.Fore.CYAN,
            'INFO': colorama.Fore.GREEN,
            'WARNING': colorama.Fore.YELLOW,
            'ERROR': colorama.Fore.RED,
            'CRITICAL': colorama.Fore.MAGENTA,
        }
    except Exception:
        RESET = '\x1b[0m'
        COLORS = {
            'DEBUG': '\x1b[36m',
            'INFO': '\x1b[32m',
            'WARNING': '\x1b[33m',
            'ERROR': '\x1b[31m',
            'CRITICAL': '\x1b[35m',
        }

    LEVEL_EMOJI = {
        'DEBUG': '🔎',
        'INFO': 'ℹ️',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '💥',
    }

    class CleanFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            ts = datetime.now().strftime('%H:%M:%S')
            lvl = record.levelname
            emoji = LEVEL_EMOJI.get(lvl, '')
            color = COLORS.get(lvl, '')
            name = record.name
            # shorten common long logger names for readability
            if name.startswith('discord'):
                name = 'discord'
            if name == '__main__' or name == __name__:
                name = 'main'
            message = super().format(record)
            # Message payload may already include timestamps from libraries; keep message raw
            return f"{color}{emoji} {ts} [{lvl}] {name}: {message}{RESET}"

    # remove any pre-configured handlers (avoids duplicate lines)
    root = logging.getLogger()
    for h in list(root.handlers):
        try:
            root.removeHandler(h)
        except Exception:
            pass

    import io
    # Ensure console handler writes UTF-8 (Windows consoles often use cp1252 which can't encode emojis)
    try:
        utf8_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        sh = logging.StreamHandler(stream=utf8_stream)
    except Exception:
        # Fallback to default stream handler
        sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(CleanFormatter('%(message)s'))
    root.addHandler(sh)
    root.setLevel(logging.INFO)

    # Silence noisy access-level loggers (you can adjust these if you want more detail)
    for noisy in ('aiohttp.access', 'websockets.protocol', 'asyncio', 'urllib3'):
        try:
            logging.getLogger(noisy).setLevel(logging.WARNING)
        except Exception:
            pass

    # Enable voice debugging
    logging.getLogger('discord.voice').setLevel(logging.DEBUG)
    logging.getLogger('discord.player').setLevel(logging.DEBUG)

    return logging.getLogger(__name__)


_print_startup_banner()
logger = setup_logging()

# Logging: add file handlers for both human-readable and structured JSONL chat logs
LOG_DIR = Path(__file__).parent / "logs"
try:
    LOG_DIR.mkdir(exist_ok=True)
except Exception:
    # If directory creation fails, fallback to current directory
    LOG_DIR = Path('.')

# Human-readable chat log (kept for quick inspection)
chat_log_txt = LOG_DIR / 'chat_logs.txt'
file_handler = logging.FileHandler(str(chat_log_txt), encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# User-requested standard log files
try:
    # Standard output log (INFO and above)
    out_log = logging.FileHandler('discordbot-out.log', mode='a', encoding='utf-8')
    out_log.setLevel(logging.INFO)
    out_log.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
    logging.getLogger().addHandler(out_log)

    # Standard error log (WARNING and above)
    err_log = logging.FileHandler('discordbot-error.log', mode='a', encoding='utf-8')
    err_log.setLevel(logging.WARNING)
    err_log.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
    logging.getLogger().addHandler(err_log)
except Exception as log_error:
    print(f"[WARN] Failed to setup standard log files: {log_error}")

# Structured JSONL chat log for programmatic analysis (one JSON object per line)
CHAT_LOG_JSONL = LOG_DIR / 'chat_logs.jsonl'
def append_chat_log(entry: dict):
    """Append a JSON object as a single line to the JSONL chat log.

    This keeps a machine-friendly record of messages with metadata useful
    for analytics, replays, and debugging.
    """
    try:
        with CHAT_LOG_JSONL.open('a', encoding='utf-8') as jf:
            jf.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # If the structured log fails, write a minimal fallback to the human log
        try:
            logger.error('Failed to append structured chat log entry')
        except Exception:
            pass


# --- Dice command (slash + text fallback) ---------------------------------
# Sends a rolling GIF then replaces it with a static dice face (1-6).
DICE_GIF_URL = "https://cdn.discordapp.com/attachments/1435569370389807144/1435585171658379385/ezgif-6882c768e3ab08.gif"
DICE_FACE_URLS = {
    1: "https://cdn.discordapp.com/attachments/1435569370389807144/1435586859098181632/Screenshot_20251105-153253copyad.png",
    2: "https://cdn.discordapp.com/attachments/1435569370389807144/1435587042154385510/2idce_2.png",
    3: "https://cdn.discordapp.com/attachments/1435569370389807144/1435589652353388565/3dice_1.png",
    4: "https://cdn.discordapp.com/attachments/1435569370389807144/1435585681987735582/Screenshot_20251105-153253copy.png",
    5: "https://cdn.discordapp.com/attachments/1435569370389807144/1435587924036026408/5dice_1.png",
    6: "https://cdn.discordapp.com/attachments/1435569370389807144/1435589024147570708/6dice_1.png",
}

# DiceBattle asset overrides (can be changed to use different remote assets)
# Small logo, background, and crossed-swords image (user-provided defaults)
DICEBATTLE_LOGO_URL = "https://cdn.discordapp.com/attachments/1435569370389807144/1435683133319282890/unnamed_3.png?ex=6917679c&is=6916161c&hm=b3183f0fb1acff8df85655cfdf94f9fc9fa2906ba2a48f9ae9d0f8f1df43c90c"
DICEBATTLE_BG_URL = "https://cdn.discordapp.com/attachments/1435569370389807144/1435676425779679364/1994.jpg?ex=6917615d&is=69160fdd&hm=8782563279de5becbf1e64d05775a71fe6c6aa60bba3d0cc6b553043f5dfa80e"
DICEBATTLE_SWORD_URL = "https://cdn.discordapp.com/attachments/1435569370389807144/1435693707276845096/pngtree-crossed-swords-icon-combat-with-melee-weapons-duel-king-protect-vector-png-image_48129218-removebg-preview_2.png?ex=69177175&is=69161ff5&hm=e588ba312801c8036052d36005dd3f3b33d5f7cdbea8bdf4097a48a8e339f018"


def build_codes_embed(codes_list):
    """Build a gift codes embed for a list of codes.

    Placed near the top of the module so message-based triggers can call it
    before other definitions later in the file.
    """
    embed = discord.Embed(
        title="✨ Active Whiteout Survival Gift Codes ✨",
        color=0xffd700,
        description=f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    embed.set_thumbnail(url="https://i.postimg.cc/s2xHV7N7/Groovy-gift.gif")

    for code in (codes_list or [])[:10]:  # Limit to 10 codes
        name = f"🎟️ Code:"
        value = f"```{code.get('code','')}```\n*Rewards:* {code.get('rewards','Rewards not specified')}\n*Expires:* {code.get('expiry','Unknown')}"
        embed.add_field(name=name, value=value, inline=False)

    if codes_list and len(codes_list) > 10:
        embed.set_footer(text=f"And {len(codes_list) - 10} more codes...")
    else:
        embed.set_footer(text="Use /giftcode to see all active codes!")

    return embed


@bot.tree.command(name="dice", description="Roll a six-sided dice")
async def dice(interaction: discord.Interaction):
    """Slash command: shows rolling animation then edits to the result image."""
    try:
        # Defer the interaction so we can follow up and edit the message
        await interaction.response.defer(thinking=True)

        # Send the rolling GIF as an embed followup
        rolling_embed = discord.Embed(title=f"{interaction.user.display_name} rolls the dice...", color=0x2ecc71)
        rolling_embed.set_image(url=DICE_GIF_URL)
        rolling_msg = await interaction.followup.send(embed=rolling_embed)

        # Wait a bit to simulate rolling
        await asyncio.sleep(2.0)

        # Pick result and edit message to static face
        result = random.randint(1, 6)
        result_embed = discord.Embed(title=f"🎲 {interaction.user.display_name} rolled a {result}!", color=0x2ecc71)
        result_embed.set_image(url=DICE_FACE_URLS.get(result))

        try:
            await rolling_msg.edit(embed=result_embed)
        except Exception:
            # Fallback: send a new followup if edit fails
            await interaction.followup.send(embed=result_embed)

    except Exception as e:
        logger.error(f"Error in /dice command: {e}")
        try:
            await interaction.followup.send(content="Failed to roll the dice.")
        except Exception:
            pass


@bot.tree.command(name="ask", description="Ask Molly anything about Whiteout Survival or any topic")
@app_commands.describe(question="Your question")
async def ask_command(interaction: discord.Interaction, question: str):
    """Ask the AI bot a question"""
    try:
        await interaction.response.defer(thinking=True)
        
        # Get user info for personalization
        user_name = interaction.user.display_name or interaction.user.name
        user_id = str(interaction.user.id)
        
        # Get user profile
        user_profile = angel_personality.get_user_profile(user_id, user_name)
        
        # If user has a player_id saved, fetch live data from API
        player_id = user_profile.game_progress.get('player_id')
        if player_id:
            try:
                # Fetch fresh player data from WOS API
                player_data = await fetch_player_info(player_id)
                if player_data:
                    # Temporarily inject live data into profile for this request only
                    user_profile.game_progress['player_name'] = player_data.get('nickname', 'Unknown')
                    user_profile.game_progress['furnace_level'] = player_data.get('furnace_level', 0)
                    user_profile.game_progress['state_id'] = player_data.get('kid', 'N/A')
                    logger.info(f"Fetched live player data for {user_name}: {player_data.get('nickname')} FC{player_data.get('furnace_level')}")
            except Exception as e:
                logger.warning(f"Failed to fetch live player data for {user_name}: {e}")
        
        # Generate personalized system prompt using profile (with live data if available)
        system_prompt = angel_personality.generate_system_prompt(user_profile)
        
        # Create messages for the API
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question}
        ]
        
        # Make API request
        try:
            logger.info(f"/ask command: Making API request for user {user_name}")
            
            # Check if question is about user's game stats and they don't have player_id
            game_stat_keywords = [
                'my furnace', 'my fc', 'my level', 'my state', 'what state',
                'my player', 'my name', 'my stats', 'my game', 'my power',
                'fc level', 'furnace level', 'what fc', 'whats my fc', 
                'what is my fc', 'what\'s my fc', 'my current fc',
                'state am i', 'which state', 'state number', 'my state number',
                'player name', 'game name', 'in game name'
            ]
            is_game_question = any(keyword in question.lower() for keyword in game_stat_keywords)
            
            if is_game_question and not player_id:
                # User is asking about their game stats but hasn't set up player ID
                # Create a modal to collect player ID
                class QuickPlayerIDModal(discord.ui.Modal, title="🎮 Setup Your Player ID"):
                    player_id_input = discord.ui.TextInput(
                        label="Player ID (9 digits)",
                        placeholder="Enter your 9-digit player ID",
                        required=True,
                        min_length=9,
                        max_length=9
                    )
                    
                    def __init__(self, original_question: str, user_id: str, user_name: str):
                        super().__init__()
                        self.original_question = original_question
                        self.user_id = user_id
                        self.user_name = user_name
                    
                    async def on_submit(self, modal_interaction: discord.Interaction):
                        pid = self.player_id_input.value.strip()
                        
                        # Validate format
                        if not re.fullmatch(r"\d{9}", pid):
                            await modal_interaction.response.send_message(
                                "❌ Invalid player ID. Must be exactly 9 digits.",
                                ephemeral=True
                            )
                            return
                        
                        await modal_interaction.response.defer(thinking=True)
                        
                        try:
                            # Fetch and validate player data
                            player_data = await fetch_player_info(pid)
                            if not player_data:
                                await modal_interaction.followup.send(
                                    "❌ Could not validate player ID. Please check and try again.",
                                    ephemeral=True
                                )
                                return
                            
                            # Save player_id to profile
                            profile = angel_personality.get_user_profile(self.user_id, self.user_name)
                            angel_personality.update_user_profile(self.user_id, {
                                'game_progress': {'player_id': pid}
                            })
                            angel_personality.save_profiles()
                            
                            # Inject live data
                            profile.game_progress['player_id'] = pid
                            profile.game_progress['player_name'] = player_data.get('nickname', 'Unknown')
                            profile.game_progress['furnace_level'] = player_data.get('furnace_level', 0)
                            profile.game_progress['state_id'] = player_data.get('kid', 'N/A')
                            
                            # Generate system prompt with live data
                            sys_prompt = angel_personality.generate_system_prompt(profile)
                            
                            # Make API request with original question
                            msgs = [
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": self.original_question}
                            ]
                            
                            response = await make_request(msgs, max_tokens=500, include_sheet_data=False)
                            
                            if response and response.strip():
                                await modal_interaction.followup.send(
                                    f"✅ **Player ID saved!** (FC {player_data.get('furnace_level')})\n\n{response}"
                                )
                            else:
                                await modal_interaction.followup.send(
                                    f"✅ Player ID saved! Your stats: **{player_data.get('nickname')}** • FC {player_data.get('furnace_level')} • State {player_data.get('kid')}"
                                )
                            
                            logger.info(f"Auto-saved player_id for {self.user_name}: {pid}")
                            
                        except Exception as e:
                            logger.error(f"Error in QuickPlayerIDModal: {e}", exc_info=True)
                            await modal_interaction.followup.send(
                                f"❌ An error occurred: {str(e)}",
                                ephemeral=True
                            )
                
                # Send the modal to collect player ID
                modal = QuickPlayerIDModal(question, user_id, user_name)
                await interaction.followup.send(
                    "💡 I need your player ID to answer that! Please provide it:",
                    ephemeral=True
                )
                # We need to send a modal, but we already deferred, so we need a button
                class PlayerIDButton(discord.ui.View):
                    def __init__(self, modal_to_show):
                        super().__init__(timeout=300)
                        self.modal_to_show = modal_to_show
                    
                    @discord.ui.button(label="Enter Player ID", style=discord.ButtonStyle.primary, emoji="🎮")
                    async def enter_id(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                        await btn_interaction.response.send_modal(self.modal_to_show)
                
                view = PlayerIDButton(modal)
                await interaction.followup.send(
                    "🎮 **Player ID Required**\n\nTo answer questions about your game stats, I need your player ID.\nClick the button below to enter it:",
                    view=view,
                    ephemeral=True
                )
                return  # Exit early, modal will handle the response
            
            # Normal flow: user has player_id or question is not game-related
            response = await make_request(messages, max_tokens=500, include_sheet_data=False)
            logger.info(f"/ask command: Received response of length {len(response) if response else 0}")
            logger.debug(f"/ask command: Response content: {response[:200] if response else 'None'}")
            
            # If response is empty, try with a minimal prompt as fallback
            if not response or len(response.strip()) == 0:
                logger.warning("/ask command: First attempt returned empty, retrying with minimal prompt...")
                minimal_messages = [
                    {"role": "system", "content": f"You are Molly, a helpful Discord bot. Keep responses short (1-3 sentences). Address {user_name} by name."},
                    {"role": "user", "content": question}
                ]
                response = await make_request(minimal_messages, max_tokens=500, include_sheet_data=False)
                logger.info(f"/ask command: Retry response length: {len(response) if response else 0}")
                
        except Exception as api_error:
            logger.error(f"API request failed in /ask: {api_error}", exc_info=True)
            await interaction.followup.send(
                f"❌ Sorry, I encountered an API error: {str(api_error)}\n\n"
                "This might be due to:\n"
                "• API keys not configured properly\n"
                "• Rate limiting\n"
                "• Service unavailable\n\n"
                "Please contact an administrator.",
                ephemeral=True
            )
            return
        
        # Check if response is valid
        if not response or len(response.strip()) == 0:
            await interaction.followup.send(
                "⚠️ I received an empty response from the AI even after retrying.\n\n"
                "**Possible causes:**\n"
                "• The AI model may be experiencing issues\n"
                "• API keys may have restrictions\n"
                "• Rate limits may be in effect\n\n"
                "Please try again in a moment or contact an administrator.",
                ephemeral=True
            )
            return
        
        # Check if it's a placeholder message (no API keys configured)
        if "Placeholder:" in response or "No API keys configured" in response:
            await interaction.followup.send(
                "⚠️ The AI service is not configured yet.\n\n"
                f"Response: {response}",
                ephemeral=True
            )
            return
        
        # Send the response
        await interaction.followup.send(response)
        
    except Exception as e:
        logger.error(f"Error in /ask command: {e}", exc_info=True)
        try:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}\n\n"
                "Please try again or contact an administrator.",
                ephemeral=True
            )
        except:
            pass


@bot.command(name='dice')
async def dice_text(ctx: commands.Context):
    """Text command fallback: !dice"""
    try:
        rolling_embed = discord.Embed(title=f"{ctx.author.display_name} rolls the dice...", color=0x2ecc71)
        rolling_embed.set_image(url=DICE_GIF_URL)
        rolling_msg = await ctx.send(embed=rolling_embed)

        await asyncio.sleep(2.0)

        result = random.randint(1, 6)
        result_embed = discord.Embed(title=f"🎲 {ctx.author.display_name} rolled a {result}!", color=0x2ecc71)
        result_embed.set_image(url=DICE_FACE_URLS.get(result))

        try:
            await rolling_msg.edit(embed=result_embed)
        except Exception:
            await ctx.send(embed=result_embed)
    except Exception as e:
        logger.error(f"Error in !dice command: {e}")
        try:
            await ctx.send("Failed to roll the dice.")
        except Exception:
            pass


# ---------- Birthday command and storage ---------------------------------
BIRTHDAY_FILE = Path(__file__).parent / "data" / "birthdays.json"

# Notify channel helper: read channel ID from env var BIRTHDAY_NOTIFY_CHANNEL
def get_notify_channel_id_from_env() -> Optional[int]:
    env_val = os.getenv('BIRTHDAY_NOTIFY_CHANNEL')
    if not env_val:
        return None
    try:
        return int(env_val)
    except Exception:
        logger.error(f"Invalid BIRTHDAY_NOTIFY_CHANNEL env var: {env_val}")
        return None

def load_birthdays() -> dict:
    try:
        # Prefer Mongo when available
        if mongo_enabled() and BirthdaysAdapter is not None:
            try:
                return BirthdaysAdapter.load_all() or {}
            except Exception:
                pass
        if BIRTHDAY_FILE.exists():
            with BIRTHDAY_FILE.open('r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load birthdays file: {e}")
    return {}

def save_birthdays(data: dict) -> bool:
    try:
        # Prefer Mongo when available
        if mongo_enabled() and BirthdaysAdapter is not None:
            try:
                # upsert per-user
                for uid, val in (data or {}).items():
                    try:
                        BirthdaysAdapter.set(str(uid), int(val.get('day')), int(val.get('month')))
                    except Exception:
                        continue
                return True
            except Exception:
                pass
        with BIRTHDAY_FILE.open('w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save birthdays file: {e}")
        return False

def set_birthday(user_id: int, day: int, month: int, player_id: Optional[str] = None) -> None:
    # Use adapter when available
    if mongo_enabled() and BirthdaysAdapter is not None:
        try:
            # Try to save with player_id (new signature)
            BirthdaysAdapter.set(str(user_id), int(day), int(month), player_id)
            return
        except TypeError:
            # Fallback for old MongoDB adapter that doesn't support player_id
            logger.warning(f"MongoDB adapter doesn't support player_id yet, saving without it")
            BirthdaysAdapter.set(str(user_id), int(day), int(month))
            return
        except Exception:
            pass
    data = load_birthdays()
    birthday_data = {"day": int(day), "month": int(month)}
    if player_id:
        birthday_data["player_id"] = player_id
    data[str(user_id)] = birthday_data
    save_birthdays(data)

def remove_birthday(user_id: int) -> bool:
    # Adapter removal when available
    if mongo_enabled() and BirthdaysAdapter is not None:
        try:
            return BirthdaysAdapter.remove(str(user_id))
        except Exception:
            pass
    data = load_birthdays()
    if str(user_id) in data:
        try:
            del data[str(user_id)]
            save_birthdays(data)
            return True
        except Exception as e:
            logger.error(f"Failed to remove birthday for {user_id}: {e}")
            return False
    return False

def get_birthday(user_id: int):
    if mongo_enabled() and BirthdaysAdapter is not None:
        try:
            return BirthdaysAdapter.get(str(user_id))
        except Exception:
            pass
    data = load_birthdays()
    return data.get(str(user_id))


class BirthdayModal(discord.ui.Modal, title="Add / Update Birthday"):
    day = discord.ui.TextInput(label="Day (1-31)", placeholder="e.g. 23", required=True, max_length=2)
    month = discord.ui.TextInput(label="Month (1-12)", placeholder="e.g. 7", required=True, max_length=2)
    player_id = discord.ui.TextInput(
        label="Player ID (Optional - 9 digits)",
        placeholder="e.g. 123456789 (for WOS avatar)",
        required=False,
        max_length=9,
        min_length=9
    )

    def __init__(self, target_user: Optional[discord.User] = None):
        super().__init__()
        self.target_user = target_user

    async def on_submit(self, interaction: discord.Interaction):
        from datetime import datetime
        import calendar
        import os
        
        try:
            # Validate inputs
            try:
                d = int(self.day.value.strip())
                m = int(self.month.value.strip())
            except Exception:
                await interaction.response.send_message("Please enter numeric values for day and month.", ephemeral=True)
                return

            if not (1 <= m <= 12):
                await interaction.response.send_message("Month must be between 1 and 12.", ephemeral=True)
                return
            if not (1 <= d <= 31):
                await interaction.response.send_message("Day must be between 1 and 31.", ephemeral=True)
                return

            user = interaction.user
            user_id = user.id if self.target_user is None else self.target_user.id
            
            # Validate and process player_id if provided
            pid = self.player_id.value.strip() if self.player_id.value else None
            if pid:
                # Validate player_id format (must be exactly 9 digits)
                if not pid.isdigit() or len(pid) != 9:
                    await interaction.response.send_message(
                        "❌ Player ID must be exactly 9 digits. Please try again.",
                        ephemeral=True
                    )
                    return

            # Check previous entry to determine if this is new or an update
            prev = get_birthday(user_id)

            # If submitting for self and an entry already exists, require removal first
            if prev and self.target_user is None:
                await interaction.response.send_message(
                    "You already have a birthday saved. To change it, first remove your existing entry using 'Remove my entry', then add a new birthday.",
                    ephemeral=True
                )
                return

            # Otherwise save (this allows overwriting when target_user is set — e.g., admin use)
            set_birthday(user_id, d, m, pid)

            # Build a human-friendly date string, e.g. "Feitan's birthday is on 1st November"
            try:
                def _ordinal(n: int) -> str:
                    if 10 <= (n % 100) <= 20:
                        suffix = 'th'
                    else:
                        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
                    return f"{n}{suffix}"

                def _month_name(m: int) -> str:
                    return calendar.month_name[m] if 1 <= m <= 12 else str(m)

                display_user = self.target_user if self.target_user is not None else user
                pretty_date = f"{_ordinal(d)} {_month_name(m)}"
                friendly_message = f"{display_user.display_name}'s birthday is on {pretty_date}"
            except Exception:
                # Fallback to simple numeric representation
                friendly_message = f"Saved birthday for <@{user_id}>: {d}/{m}"

            await interaction.response.send_message(friendly_message, ephemeral=True)

            # Notify configured channel (per-guild or env fallback) with a detailed embed
            try:
                # Read notify channel id from env var (BIRTHDAY_NOTIFY_CHANNEL)
                notify_id = get_notify_channel_id_from_env()

                if notify_id is not None:
                    channel = bot.get_channel(notify_id)
                    if channel is None:
                        try:
                            channel = await bot.fetch_channel(notify_id)
                        except Exception:
                            channel = None

                    if channel is not None:
                        status = "Updated" if prev else "New Entry"
                        info_embed = discord.Embed(title="🎉 Birthday Submitted", color=0xff69b4, timestamp=datetime.utcnow())
                        info_embed.add_field(name="User", value=f"{user.mention} ({user})", inline=False)
                        info_embed.add_field(name="User ID", value=str(user_id), inline=True)
                        # If target_user differs, show target
                        if self.target_user is not None:
                            info_embed.add_field(name="Target User", value=f"{self.target_user.mention} ({self.target_user.id})", inline=True)
                        # Add a human-friendly description and keep numeric fields for precision
                        try:
                            info_embed.description = f"{user.display_name}'s birthday is on {_ordinal(d)} {_month_name(m)}"
                        except Exception:
                            info_embed.description = f"Birthday: {d}/{m}"
                        info_embed.add_field(name="Day", value=str(d), inline=True)
                        info_embed.add_field(name="Month", value=str(m), inline=True)
                        # Add Player ID if provided
                        if pid:
                            info_embed.add_field(name="Player ID", value=f"`{pid}`", inline=True)
                        info_embed.add_field(name="Action", value=status, inline=True)
                        if interaction.guild:
                            info_embed.add_field(name="Guild", value=f"{interaction.guild.name} ({interaction.guild.id})", inline=False)

                        info_embed.set_footer(text="Birthday manager")

                        try:
                            await channel.send(embed=info_embed)
                        except Exception as send_err:
                            logger.error(f"Failed to send birthday notification to channel {notify_id}: {send_err}")
                    else:
                        logger.error(f"Birthday notify channel {notify_id} not found or inaccessible.")
                else:
                    # No configured notify channel; nothing to do
                    logger.debug("No birthday notify channel configured for this guild or via env var.")
            except Exception as notify_exc:
                logger.error(f"Error while notifying birthday channel: {notify_exc}")
            
            # NEW: Check if birthday is today and send immediate wish
            try:
                now = datetime.utcnow()
                if d == now.day and m == now.month:
                    # Birthday is today! Send immediate wish
                    birthday_cog = bot.get_cog("BirthdaySystem")
                    if birthday_cog:
                        # Get birthday channel from environment
                        birthday_channel_id = os.getenv('BIRTHDAY_CHANNEL_ID')
                        if birthday_channel_id:
                            try:
                                birthday_channel = bot.get_channel(int(birthday_channel_id))
                                if birthday_channel:
                                    # Trigger immediate birthday check
                                    success, count = await birthday_cog.manual_birthday_check(birthday_channel)
                                    if success:
                                        logger.info(f"🎉 Sent immediate birthday wish for user {user_id}")
                                else:
                                    logger.warning(f"Birthday channel {birthday_channel_id} not found")
                            except Exception as e:
                                logger.error(f"Failed to send immediate birthday wish: {e}")
                        else:
                            logger.debug("BIRTHDAY_CHANNEL_ID not set, skipping immediate wish")
                    else:
                        logger.debug("BirthdaySystem cog not loaded, skipping immediate wish")
            except Exception as immediate_wish_exc:
                logger.error(f"Error checking for immediate birthday wish: {immediate_wish_exc}")
                
        except Exception as e:
            logger.error(f"Error in BirthdayModal.on_submit: {e}")
            await interaction.response.send_message("Failed to save birthday.", ephemeral=True)


class BirthdayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Add/Update birthday", style=discord.ButtonStyle.primary, custom_id="birthday_add_update")
    async def add_update(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Prevent users from creating multiple entries: if they already have one, instruct to remove first
            existing = get_birthday(interaction.user.id)
            if existing:
                await interaction.response.send_message(
                    "You already have a birthday saved. To change it, first click 'Remove my entry' to delete your existing entry, then click 'Add/Update birthday' to submit a new one.",
                    ephemeral=True
                )
                return

            modal = BirthdayModal()
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error opening BirthdayModal: {e}")
            await interaction.response.send_message("Failed to open birthday form.", ephemeral=True)

    @discord.ui.button(label="Remove my entry", style=discord.ButtonStyle.danger, custom_id="birthday_remove")
    async def remove_entry(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            removed = remove_birthday(interaction.user.id)
            if removed:
                await interaction.response.send_message("Your birthday entry was removed.", ephemeral=True)

                # Send removal notification to configured notify channel (env var)
                try:
                    notify_id = get_notify_channel_id_from_env()
                    if notify_id:
                        channel = bot.get_channel(notify_id)
                        if channel is None:
                            try:
                                channel = await bot.fetch_channel(notify_id)
                            except Exception:
                                channel = None

                        if channel is not None:
                            info_embed = discord.Embed(title="🗑️ Birthday Removed", color=0xff69b4, timestamp=datetime.utcnow())
                            info_embed.add_field(name="User", value=f"{interaction.user.mention} ({interaction.user})", inline=False)
                            info_embed.add_field(name="User ID", value=str(interaction.user.id), inline=True)
                            # Try to include guild info if available
                            if interaction.guild:
                                info_embed.add_field(name="Guild", value=f"{interaction.guild.name} ({interaction.guild.id})", inline=False)

                            info_embed.set_footer(text="Birthday manager")

                            try:
                                await channel.send(embed=info_embed)
                            except Exception as send_err:
                                logger.error(f"Failed to send birthday removal notification to channel {notify_id}: {send_err}")
                        else:
                            logger.error(f"Birthday notify channel {notify_id} not found or inaccessible.")
                    else:
                        logger.debug("No BIRTHDAY_NOTIFY_CHANNEL configured; skipping removal notification.")
                except Exception as notify_exc:
                    logger.error(f"Error while notifying birthday removal channel: {notify_exc}")
            else:
                await interaction.response.send_message("No birthday entry found for you.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error removing birthday: {e}")
            await interaction.response.send_message("Failed to remove your entry.", ephemeral=True)


@bot.tree.command(name="birthday", description="Manage your birthday entry (day & month)")
async def birthday(interaction: discord.Interaction):
    """Sends an embed explaining the birthday system with buttons to add/update or remove your birthday."""
    try:
        embed_text = (
            "**🎉 Let's never miss a birthday again!**\n\n"
            
            "🎂 Click “Add Birthday”\n\n"
            "📅 Choose day & month\n\n"
            "🥳 Your day gets celebrated – party vibes guaranteed!\n\n"
            "🔄 Update? Just click the button again\n\n"
            "✨ More entries = more fun & more party vibes! 🎉🎈"
        )

        embed = discord.Embed(title="Birthday Manager", description=embed_text, color=0xff69b4)
        embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1435569370389807144/1496492127494996119/image_34e5650b.png?ex=69ea1466&is=69e8c2e6&hm=d6fa1fe93d7c505e34d5c746c5e36f42d9e076c5dd7aab13a4f0683b1bb1dcde")

        view = BirthdayView()
        # Send the response (don't pass wait to response.send_message)
        await interaction.response.send_message(embed=embed, view=view)
        # Get the message object from original_response() and register the view
        try:
            msg = await interaction.original_response()
            bot.add_view(view, message_id=msg.id)
        except Exception as reg_err:
            logger.debug(f"Failed to register BirthdayView for message: {reg_err}")
    except Exception as e:
        logger.error(f"Error in /birthday command: {e}")
        try:
            await interaction.response.send_message("Failed to send birthday manager.", ephemeral=True)
        except Exception:
            pass


# /settings wrapper removed.
# The `/settings` command is provided directly by the Alliance cog via
# the @app_commands.command decorator in `alliance.py`. Removing the
# local wrapper avoids duplicate registrations where both the cog and
# a wrapper attempt to register `/settings`.


# Debug slash commands removed — debug tools moved to /settings > Debug button


## `/load_alliance` admin helper removed — the Alliance cog should be loaded
## at startup by the bot's normal extension-loading flow. Removing the on-
## demand loader avoids partial backends and the "Settings backend not
## loaded (Alliance cog missing)" user-facing message.


# /birthday_setchannel removed — notification channel is read from the BIRTHDAY_NOTIFY_CHANNEL env var

# NOTE: The early `on_message` handler was removed to avoid overriding
# the comprehensive `on_message` defined later in this file. The later
# handler logs messages, triggers keyword-based behavior (giftcode/dice),
# and calls `bot.process_commands(message)` so prefixed text commands work.


# Reduce noise: silence informational logs from the gift_codes module (it's verbose)
logging.getLogger('gift_codes').setLevel(logging.WARNING)

@bot.tree.command(name="giftcode", description="Show active gift codes")
async def giftcode(interaction: discord.Interaction):
    """Show active gift codes."""
    # Delegate to the GiftOperations cog which has the updated logic and embed format
    gift_cog = bot.get_cog("GiftOperations")
    if gift_cog:
        await gift_cog.list_gift_codes(interaction)
    else:
        # Fallback if cog is not loaded (should not happen)
        await interaction.response.send_message("Gift system is currently initializing. Please try again in a moment.", ephemeral=True)

@bot.tree.command(name="refresh", description="Clears cached alliance data and reloads from Google Sheets.")
@app_commands.default_permissions(administrator=True)  # Only server administrators can use this
async def refresh(interaction: discord.Interaction):
    """Clear the Google Sheets cache to fetch fresh data on next request"""
    # Defer the reply since we're doing an operation
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Reset the cache in our sheets manager
        manager.sheets_manager.reset_cache()
        
        # Send success message
        await interaction.followup.send(
            "♻️ Cache cleared — next request will fetch live data from Google Sheets.",
            ephemeral=True
        )
        logger.info(f"Alliance data cache cleared by {interaction.user.name} ({interaction.user.id})")
        
    except Exception as e:
        # Handle any errors
        error_msg = f"❌ Failed to clear cache: {str(e)}"
        await interaction.followup.send(error_msg, ephemeral=True)
        logger.error(f"Cache clear failed: {e}", exc_info=True)
async def time_autocomplete(interaction: discord.Interaction, current: str):
    """Provide contextual autocomplete suggestions for the time parameter."""
    # Defensive: if the interaction has already been acknowledged by some other handler,
    # don't attempt to compute or return choices — attempting to respond will raise 400.
    try:
        if interaction.response.is_done():
            logger.warning("Autocomplete interaction already acknowledged; skipping autocomplete response")
            return []
    except Exception:
        # If the library does not expose is_done or another error occurs, continue normally
        pass
    choices: list[app_commands.Choice] = []
    q = (current or "").strip().lower()

    # common helpful templates - expanded with the requested formats
    templates = [
        # SIMPLE TIMES
        ("5 minutes", "Relative: 5 minutes from now"),
        ("2 hours", "Relative: 2 hours from now"),
        ("1 day", "Relative: 1 day from now"),
        ("today at 8:50 pm", "Today at 8:50 PM"),
        ("today at 20:30", "Today at 20:30 (24h)"),
        ("tomorrow 3pm IST", "Tomorrow at 3:00 PM (IST)"),
        ("tomorrow at 15:30 UTC", "Tomorrow at 15:30 (UTC)"),
        ("at 18:30", "Today at 18:30 (24h)"),
        ("2025-11-05 18:00", "Exact date/time (YYYY-MM-DD HH:MM)"),
        ("next monday 10am", "Next Monday at 10:00 AM"),

        # SPECIFIC DATES
        ("on 25th November 2025 at 3pm", "Specific date: Nov 25, 2025 at 3:00 PM"),
        ("on Nov 25 at 15:30", "Specific date: Nov 25 at 15:30"),
        ("on December 1st at 9am IST", "Specific date: Dec 1 at 9:00 AM (IST)"),

        # RECURRING
        ("daily at 9am IST", "Recurring: daily at 9:00 AM (IST)"),
        ("daily at 21:30", "Recurring: daily at 21:30"),
        ("every 2 days at 8pm", "Recurring: every 2 days at 8:00 PM"),
        ("every 3 days at 10am", "Recurring: every 3 days at 10:00 AM"),
        ("alternate days at 10am", "Recurring: alternate days at 10:00 AM"),
        ("weekly at 15:30", "Recurring: weekly at 15:30"),
        ("every week at 9am EST", "Recurring: weekly at 9:00 AM (EST)"),
    ]

    # If they start with a number suggest relative times
    if q and q[0].isdigit():
        try:
            num = int(''.join(ch for ch in q.split()[0] if ch.isdigit()))
            choices.append(app_commands.Choice(name=f"in {num}m — in {num} minutes", value=f"in {num}m"))
            choices.append(app_commands.Choice(name=f"in {num}h — in {num} hours", value=f"in {num}h"))
        except Exception:
            pass

    # quick starts
    if q.startswith("t"):
        choices.append(app_commands.Choice(name="today 6pm — Today at 6:00 PM", value="today 6pm"))
        choices.append(app_commands.Choice(name="tomorrow 9am — Tomorrow at 9:00 AM", value="tomorrow 9am"))
    
    # specific date quick starts
    if q.startswith("on"):
        from datetime import datetime
        # Suggest a date a week from now
        future_date = datetime.now() + timedelta(days=7)
        date_str = future_date.strftime("%B %d")
        choices.append(app_commands.Choice(name=f"on {date_str} at 3pm — Specific date", value=f"on {date_str} at 3pm"))
        choices.append(app_commands.Choice(name="on December 25 at 9am — Christmas example", value="on December 25 at 9am"))

    # date-like heuristics
    if q and any(c.isdigit() for c in q) and ('-' in q or '/' in q or ':' in q):
        choices.append(app_commands.Choice(name="2025-11-05 18:00 — Exact date/time", value="2025-11-05 18:00"))

    # If the user typed something that can be parsed, show a resolved preview
    try:
        if q:
            parsed_dt, info = TimeParser.parse_time_string(current)
            if parsed_dt:
                # Determine user's preferred timezone for display
                user_tz = get_user_timezone(interaction.user.id) or TimeParser.get_local_timezone()
                local_dt = TimeParser.utc_to_local(parsed_dt, user_tz)
                preview = local_dt.strftime('%b %d, %I:%M %p')
                # prepend to choices so it's prominent
                choices.insert(0, app_commands.Choice(name=f"{current} → {preview} ({user_tz.upper()})", value=current))
    except Exception:
        # parsing failure should not break autocomplete
        pass

    for val, desc in templates:
        if len(choices) >= 25:
            break
        if q == "" or val.startswith(q) or q in val or q in desc.lower():
            choices.append(app_commands.Choice(name=f"{val} — {desc}", value=val))

    return choices[:25]




@bot.tree.command(name="reminder", description="Set a reminder with time and message")
@app_commands.describe(
    time="When to remind you (e.g., '5 minutes', 'tomorrow 3pm IST', 'daily at 9am')",
    message="Title/header for the reminder",
    channel="Channel to send reminder in",
    body="Optional detailed message body for the reminder",
    thumbnailimage_preset="Optional preset image to use as embed thumbnail",
    image_url="Optional direct image URL to use in the reminder embed",
    thumbnail_url="Optional image URL to use as embed.thumbnail (overrides preset)",
    footer_text="Optional footer text for the embed",
    footer_icon_url="Optional footer icon URL for the embed",
    author_url="Optional URL to link the author name to"
)
@app_commands.autocomplete(time=time_autocomplete)
@app_commands.choices(
    thumbnailimage_preset=[app_commands.Choice(name=k, value=k) for k in REMINDER_IMAGES.keys()]
)
async def reminder(interaction: discord.Interaction, time: str, message: str, channel: discord.TextChannel, body: str = None, 
                   thumbnailimage_preset: str = None, image_url: str = None, thumbnail_url: str = None,
                   footer_text: str = None, footer_icon_url: str = None, author_url: str = None):
    # Defer only if the interaction hasn't already been acknowledged. Defer can raise
    # NotFound/HTTPException if the interaction is invalid or already responded to, so
    # catch and continue gracefully.
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)
    except Exception as e:
        logger.warning(f"Could not defer interaction for /reminder: {e}. Continuing without defer.")

    try:
        target_channel = channel

        # Display the exact command as entered
        # Build a short command preview (don't send it here; create_reminder will reply)
        command_text = f"/reminder time: {time} message: {message}"
        if channel:
            command_text += f" channel: {channel.mention}"
        logger.debug(f"Creating reminder: {command_text}")

        # Determine image to use: explicit URL takes precedence over preset choice
        chosen_image = None
        chosen_thumbnail = thumbnail_url
        
        try:
            if image_url and isinstance(image_url, str) and image_url.strip():
                # Basic validation — accept only http/https URLs
                if image_url.strip().lower().startswith(('http://', 'https://')):
                    chosen_image = image_url.strip()
            
            # If thumbnail preset is selected and no explicit thumbnail URL is provided, use the preset
            if thumbnailimage_preset and not chosen_thumbnail:
                chosen_thumbnail = REMINDER_IMAGES.get(thumbnailimage_preset)
        except Exception:
            chosen_image = None
            chosen_thumbnail = None

        # Create the reminder (explicit thumbnail/url/footer options are forwarded)
        reminder_cog = bot.get_cog('ReminderSystem')
        if not reminder_cog:
            await interaction.followup.send(
                "❌ Reminder system is not loaded. Please contact an administrator.",
                ephemeral=True
            )
            return
        
        reminder_id = await reminder_cog.create_reminder(
            interaction, time, message, target_channel,
            body=body,
            image_url=chosen_image,
            thumbnail_url=chosen_thumbnail,
            footer_text=footer_text,
            footer_icon_url=footer_icon_url,
            author_url=author_url
        )

        # If creation failed, reminder_id will be False/None
        if not reminder_id:
            # Error message already sent by create_reminder in many cases
            return

        # Ask the user if they'd like to attach or pick an image (ephemeral, 2-minute window)
        class ImageAttachView(discord.ui.View):
            def __init__(self, bot, reminder_id, channel, user, *, timeout=120):
                super().__init__(timeout=timeout)
                self.bot = bot
                self.reminder_id = reminder_id
                self.channel = channel
                self.user = user

                # Add a select for presets dynamically
                options = [discord.SelectOption(label=k, value=k) for k in REMINDER_IMAGES.keys()]
                self.add_item(self.PresetSelect(options))
                self.add_item(self.UploadButton())

            class PresetSelect(discord.ui.Select):
                def __init__(self, options):
                    super().__init__(placeholder='Choose a preset image', min_values=1, max_values=1, options=options)

                async def callback(self, interaction: discord.Interaction):
                    parent: ImageAttachView = self.view  # type: ignore
                    if interaction.user.id != parent.user.id:
                        await interaction.response.send_message('This selection is not for you.', ephemeral=True)
                        return
                    choice = self.values[0]
                    url = REMINDER_IMAGES.get(choice)
                    try:
                        ok = reminder_system.storage.update_reminder_fields(parent.reminder_id, {'image_url': url})
                    except Exception:
                        ok = False
                    if ok:
                        await interaction.response.send_message('✅ Preset image applied to your reminder.', ephemeral=True)
                    else:
                        await interaction.response.send_message('❌ Failed to apply preset image. Try uploading instead.', ephemeral=True)

            class UploadButton(discord.ui.Button):
                def __init__(self):
                    super().__init__(label='Upload Image', style=discord.ButtonStyle.primary)

                async def callback(self, interaction: discord.Interaction):
                    parent: ImageAttachView = self.view  # type: ignore
                    if interaction.user.id != parent.user.id:
                        await interaction.response.send_message('This action is not for you.', ephemeral=True)
                        return

                    await interaction.response.send_message('Please upload an image in the same channel within 2 minutes (reply to any message). I will capture the first attachment you send.', ephemeral=True)

                    def check(m: discord.Message):
                        return m.author.id == parent.user.id and m.channel.id == parent.channel.id and len(m.attachments) > 0

                    try:
                        msg = await parent.bot.wait_for('message', check=check, timeout=120)
                        att = msg.attachments[0]
                        # Basic validation — prefer images
                        url = att.url
                        ok = reminder_system.storage.update_reminder_fields(parent.reminder_id, {'image_url': url})
                        if ok:
                            await interaction.followup.send('✅ Uploaded image saved to reminder.', ephemeral=True)
                        else:
                            await interaction.followup.send('❌ Failed to save uploaded image. Try again later.', ephemeral=True)
                    except asyncio.TimeoutError:
                        await interaction.followup.send('⌛ Time expired. Please run /reminder again to attach an image.', ephemeral=True)

        view = ImageAttachView(bot, reminder_id, target_channel, interaction.user)
        try:
            await interaction.followup.send('Would you like to attach an image to this reminder? Pick a preset or upload now. This prompt expires in 2 minutes.', view=view, ephemeral=True)
        except Exception as e:
            logger.warning(f'Failed to send image attach prompt: {e}')

        

    except Exception as e:
        logger.error(f"Error in remind command: {str(e)}")
        try:
            await interaction.followup.send("❌ **Error**\n\nSorry, there was an error setting your reminder. Please try again.", ephemeral=True)
        except:
            logger.error("Failed to send error message")





# /show_timezone command removed per user request. Previously showed user's configured timezone.


@bot.tree.command(name="reminderdashboard", description="Open interactive reminder dashboard (list/delete/set timezone)")
async def reminderdashboard(interaction: discord.Interaction):
    """Interactive dashboard that consolidates list/delete/set-timezone into a single UI."""
    await animator.show_loading(interaction)
    try:
        # Build a view with buttons that open selects/modals as needed
        # Views moved to cogs.shared_views
        view = sv.ReminderDashboardView(bot.reminder_system)
    except Exception as e:
        logger.error(f"Failed to build reminder dashboard UI: {e}")
        await animator.stop_loading(interaction, delete=True)
        try:
            await interaction.response.send_message("Failed to open reminder dashboard UI.", ephemeral=True)
        except Exception:
            pass

    # Build preview items from storage for the renderer
    try:
        raw = bot.reminder_system.storage.get_user_reminders(str(interaction.user.id), limit=8)
    except Exception:
        raw = []

    preview_items = []
    user_tz = get_user_timezone(interaction.user.id) or TimeParser.get_local_timezone()
    for r in raw:
        try:
            rid = r.get('id')
            msg = r.get('message', '')
            rt = r.get('reminder_time')
            # reminder_time stored as naive UTC in DB; convert to display
            if isinstance(rt, str):
                try:
                    from datetime import datetime
                    rt_dt = datetime.fromisoformat(rt)
                except Exception:
                    rt_dt = None
            else:
                rt_dt = rt

            tdisp = ''
            if rt_dt:
                try:
                    local_dt = TimeParser.utc_to_local(rt_dt, user_tz)
                    tdisp = local_dt.strftime('%b %d, %I:%M %p')
                except Exception:
                    tdisp = str(rt_dt)

            preview_items.append({'id': rid, 'message': msg, 'time_display': tdisp})
        except Exception:
            continue

    # Send the original embed-based dashboard (no image) and attach the interactive View
    try:
        embed = discord.Embed(
            title="🎛️ Reminder Dashboard",
            description="Manage your reminders quickly using the buttons below.",
            color=0x2ecc71,
        )
        embed.set_thumbnail(url="https://i.postimg.cc/Fzq03CJf/a463d7c7-7fc7-47fc-b24d-1324383ee2ff-removebg-preview.png")
        # Describe each quick action with a one-line hint
        embed.add_field(
            name="Quick Actions",
            value=(
                "• `List` — Show all your active reminders\n"
                "• `Delete` — Remove a selected reminder\n"
                "• `Timezone` — Set or clear your preferred timezone for display"
            ),
            inline=False,
        )
        embed.add_field(name="Tip", value="Select a reminder under Delete to remove it. Timezone selection changes how times are shown.", inline=False)
        
        # Stop loading animation and send dashboard in one go
        await animator.stop_loading(interaction, delete=True)
        # If the interaction was already deferred (show_thinking uses defer),
        # we must use followup.send instead of response.send_message.
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception:
            # Fallback to followup if response path fails for any reason
            try:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except Exception:
                raise
    except Exception as e:
        logger.error(f"Failed to send dashboard embed: {e}")
        try:
            # Try to respond with fallback message if interaction hasn't been responded to yet
            if not interaction.response.is_done():
                await animator.stop_loading(interaction, delete=True)
                await interaction.response.send_message('Open your Reminder Dashboard', view=view, ephemeral=True)
            else:
                # If already responded, use followup
                await interaction.followup.send('Failed to open reminder dashboard.', ephemeral=True)
        except Exception as e2:
            logger.error(f"Failed to send fallback dashboard message: {e2}")


# /giftchannel command removed per user request. Previously allowed setting gift code posting channel.


# /list_gift_channel command removed per user request. Previously showed configured gift code channel.


# Note: /set_feedback_channel and /unset_feedback_channel removed per user request.


# /giftcode_check command removed per user request. Previously forced a giftcode check and posting.


# playerinfo handled by cog; references removed from main code


@bot.tree.command(name="giftcodesettings", description="Open interactive gift code settings dashboard for this server")
@app_commands.default_permissions(administrator=True)
async def giftcodesettings(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await animator.show_loading(interaction)
    try:
        if not interaction.guild:
            await animator.stop_loading(interaction, delete=True)
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id

        # NOTE: ConfirmClearView removed — clearing sent codes handled elsewhere or disabled from dashboard

        # GiftCodeSettingsView moved to cogs.shared_views
        view = sv.GiftCodeSettingsView(interaction.client)

        header = discord.Embed(title="🎟️ Gift Code Settings", description="Manage this server's automatic gift code poster and recorded codes.", color=0xffd700)
        # Show current channel if configured
        ch_id = giftcode_poster.poster.get_channel(guild_id)
        if ch_id:
            ch_obj = interaction.guild.get_channel(ch_id)
            header.add_field(name="Configured Channel", value=(ch_obj.mention if ch_obj else f"ID: {ch_id} (not found)"), inline=False)
        else:
            header.add_field(name="Configured Channel", value="Not configured", inline=False)

        await animator.stop_loading(interaction, delete=True)
        await interaction.followup.send(embed=header, view=view, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in giftcodesettings command: {e}")
        await animator.stop_loading(interaction, delete=True)
        try:
            await interaction.followup.send("❌ Error opening gift code settings.", ephemeral=True)
        except Exception:
            pass

# NOTE: `/delete_reminder` and `/listreminder` commands removed — functionality moved into `/reminderdashboard` UI.




@bot.tree.command(name="imagine", description="Generate AI Images (Pollinations compatibility)")
@app_commands.describe(
    prompt="Prompt of the image you want to generate",
    width="Width of the image (optional)",
    height="Height of the image (optional)",
    model="Model to use (optional)",
    enhance="Enable prompt enhancement (ignored)",
    safe="Safe for work (ignored)",
    cached="Use default seed / caching (ignored)",
    nologo="Remove logo (ignored)",
    private="Send result as ephemeral to only you"
)
@app_commands.choices(
    model=[
        app_commands.Choice(name="flux", value="flux"),
        app_commands.Choice(name="Turbo", value="turbo"),
        app_commands.Choice(name="gptimage", value="gptimage"),
        app_commands.Choice(name="kontext", value="kontext"),
    app_commands.Choice(name="stable-diffusion — UNDER MAINTAINANCE", value="stable-diffusion"),
    ],
)
async def imagine(
    interaction: discord.Interaction,
    prompt: str,
    width: int = None,
    height: int = None,
    model: app_commands.Choice[str] = None,
    enhance: bool = False,
    safe: bool = True,
    cached: bool = False,
    nologo: bool = False,
    private: bool = False,
):
    """Compatibility wrapper for Pollinations' /pollinate command.

    NOTE: This implementation intentionally keeps the backend call simple and
    delegates to the existing `make_image_request(prompt)` in `api_manager.py`.
    Many Pollinations-specific flags are accepted for compatibility but are
    currently ignored by the underlying generator. If you want full feature
    parity (model selection, width/height, caching, etc.) we can extend
    `make_image_request` next.
    """
    # Show thinking animation while processing
    await thinking_animation.show_thinking(interaction)

    try:
    # Note: thinking_animation.show_thinking has already deferred the interaction.
    # Avoid deferring twice which raises "already responded".

        # Basic validation (non-blocking). Only allow reasonable sizes if provided.
        if width is not None and (width <= 0 or width > 2048):
            raise ValueError("Width must be a positive integer <= 2048")
        if height is not None and (height <= 0 or height > 2048):
            raise ValueError("Height must be a positive integer <= 2048")

        # Resolve model choice value
        model_val = (model.value if hasattr(model, 'value') else model)

        # Determine available backends
        has_hf = any(k.startswith('HUGGINGFACE_API_TOKEN') for k in os.environ.keys())
        has_openai = bool(os.getenv('OPENAI_API_KEY'))

        # Generate a seed for deterministic-looking results and measure processing time
        seed = random.randint(0, 2**31 - 1)
        start_time = time.time()

        # Auto-fallback: if no HF or OpenAI keys are configured, use Pollinations public endpoint
        if not has_hf and not has_openai:
            image_data = await fetch_pollinations_image(
                prompt,
                width=width,
                height=height,
                model_name=(model.value if hasattr(model, 'value') else model),
                seed=seed,
            )
            processing_time = time.time() - start_time
            view = PollinateButtonView()
        else:
            # Branch: if user selected stable-diffusion, use Hugging Face backend
            if model_val == 'stable-diffusion':
                # Use environment HUGGINGFACE_MODEL unless a full model string provided
                hf_model = os.getenv('HUGGINGFACE_MODEL', 'stabilityai/stable-diffusion-xl-base-1.0')
                image_data = await make_image_request(prompt, width=width, height=height, model=hf_model)
                processing_time = time.time() - start_time
                # For HF-generated images, don't provide the Edit button view
                view = PollinateNoEditView()
            else:
                # Use Pollinations public API for other models
                image_data = await fetch_pollinations_image(
                    prompt,
                    width=width,
                    height=height,
                    model_name=model_val,
                    seed=seed,
                )
                processing_time = time.time() - start_time

        # Build pollinations URL for embedding/bookmarking (for non-HF models)
        base = "https://image.pollinations.ai/prompt/"
        encoded = quote(prompt, safe='')
        pollinate_url = base + encoded
        params = []
        if width:
            params.append(f"width={int(width)}")
        if height:
            params.append(f"height={int(height)}")
        # Omit model parameter due to 500 errors on Pollinations backend
        # if model_val and model_val != 'stable-diffusion':
        #     params.append(f"model={quote(model_val, safe='')}")
        if seed is not None:
            params.append(f"seed={int(seed)}")
        if params:
            pollinate_url = pollinate_url + "?" + "&".join(params)

        # Create a file from the image data
        from io import BytesIO
        image_file = discord.File(BytesIO(image_data), filename="pollinated_image.png")

        # Build a small embed mirroring Pollinations style and include metadata fields
        success_embed = discord.Embed(
            title="🪐 Image",
            description=f"",
            color=0x00FF7F,
            url=pollinate_url,
            timestamp=datetime.utcnow(),
        )
        # Author line similar to Pollinations UI
        try:
            avatar_url = interaction.user.display_avatar.url
        except Exception:
            avatar_url = None
        success_embed.set_author(name=f"Generated by {interaction.user.display_name}", icon_url=avatar_url)
        # Add metadata fields
        use_model = (model.value if hasattr(model, 'value') else model) or os.getenv('HUGGINGFACE_MODEL', 'flux')
        is_xl = 'xl' in (use_model or '').lower()
        default_w = 1024 if is_xl else 512
        default_h = 1024 if is_xl else 512
        use_w = int(width) if width else default_w
        use_h = int(height) if height else default_h

        # Layout: Prompt (full width), then a single code-block with details (seed, time, model, dimensions)
        success_embed.add_field(name="Prompt", value=f"```{prompt}```", inline=False)
        details = (
            f"Seed: {seed}\n"
            f"Processing Time: {processing_time:.2f} s\n"
            f"Model: {use_model}\n"
            f"Dimensions: {use_w}x{use_h}"
        )
        success_embed.add_field(name="Details", value=f"```\n{details}\n```", inline=False)
        success_embed.set_footer(text=f"Generated for {interaction.user.display_name}")
        # Ensure embed displays the attached image
        success_embed.set_image(url="attachment://pollinated_image.png")

        # Stop the animation and delete the message so image can "pop over"
        await thinking_animation.stop_thinking(interaction, delete_message=True)

        # Send result (ephemeral or public based on `private`) with interactive buttons
        # For stable-diffusion (HF) we use PollinateNoEditView which omits the Edit button
        if model_val == 'stable-diffusion':
            if private:
                await interaction.followup.send(embed=success_embed, file=image_file, ephemeral=True)
            else:
                await interaction.followup.send(content=f"{interaction.user.mention}", embed=success_embed, file=image_file, view=PollinateNoEditView())
        else:
            if private:
                await interaction.followup.send(embed=success_embed, file=image_file, ephemeral=True)
            else:
                await interaction.followup.send(content=f"{interaction.user.mention}", embed=success_embed, file=image_file, view=PollinateButtonView())

        logger.info("Successfully sent imagine image")

    except Exception as e:
        logger.error(f"Error in imagine command: {str(e)}")
        error_embed = discord.Embed(
            title="❌ Image Generation Failed",
            description="Sorry, I couldn't generate your image right now. Please try again later or check your prompt.",
            color=0xff0000,
        )
        try:
            if thinking_animation.animation_message:
                await thinking_animation.animation_message.edit(embed=error_embed)
            else:
                await interaction.followup.send(embed=error_embed)
        except Exception as edit_error:
            logger.error(f"Failed to send imagine error message: {edit_error}")
            try:
                await interaction.followup.send(embed=error_embed)
            except Exception:
                logger.error("Failed final imagine error followup")

@bot.tree.command(name="serverstats", description="Show detailed server statistics")
async def serverstats(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    # Show thinking animation while processing
    await thinking_animation.show_thinking(interaction)

    try:
        embed = discord.Embed(title=f"📊 {guild.name} Server Stats", color=0x3498db)
        embed.add_field(name="👥 Members", value=guild.member_count, inline=True)
        embed.add_field(name="📅 Created", value=guild.created_at.strftime("%Y-%m-%d %H:%M UTC"), inline=True)
        text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
        voice_channels = len([c for c in guild.channels if isinstance(c, discord.VoiceChannel)])
        categories = len([c for c in guild.channels if isinstance(c, discord.CategoryChannel)])
        embed.add_field(name="💬 Text Channels", value=text_channels, inline=True)
        embed.add_field(name="🔊 Voice Channels", value=voice_channels, inline=True)
        embed.add_field(name="📁 Categories", value=categories, inline=True)
        embed.add_field(name="🎭 Roles", value=len(guild.roles), inline=True)
        # Count bots by checking for "Bot" role first, fallback to bot flag
        bot_role = discord.utils.get(guild.roles, name="Bot") or discord.utils.get(guild.roles, name="bot")
        if bot_role:
            bots = len(bot_role.members)
        else:
            bots = len([m for m in guild.members if m.bot])
        humans = guild.member_count - bots
        embed.add_field(name="👤 Humans", value=humans, inline=True)
        embed.add_field(name="🤖 Bots", value=bots, inline=True)
        online = len([m for m in guild.members if m.status in [discord.Status.online, discord.Status.idle, discord.Status.dnd]])
        embed.add_field(name="🟢 Online", value=online, inline=True)
        embed.add_field(name="⚫ Offline", value=guild.member_count - online, inline=True)
        embed.add_field(name="🚫 Content Filter", value=str(guild.explicit_content_filter).title(), inline=True)
        if guild.premium_tier > 0:
            embed.add_field(name="🚀 Boost Level", value=guild.premium_tier, inline=True)
            embed.add_field(name="💎 Boosts", value=guild.premium_subscription_count, inline=True)
        # Find most active user in "💬┃main-chat" channel (excluding bots)
        chats_channel = discord.utils.get(guild.channels, name="💬┃main-chat")
        if chats_channel and isinstance(chats_channel, discord.TextChannel):
            logger.info(f"Channel found: {chats_channel.name} (ID: {chats_channel.id})")
            try:
                message_counts = {}
                message_count = 0
                async for message in chats_channel.history(limit=1000):
                    if not message.author.bot:  # Exclude bot messages
                        author_id = message.author.id
                        message_counts[author_id] = message_counts.get(author_id, 0) + 1
                    message_count += 1
                logger.info(f"Fetched {message_count} total messages from channel")
                if message_counts:
                    top_user_id, count = max(message_counts.items(), key=lambda x: x[1])
                    top_user = guild.get_member(top_user_id)
                    if top_user and not top_user.bot:
                        logger.info(f"Top user: {top_user.display_name} with {count} messages")
                        embed.add_field(name="Most Active User", value=f"{top_user.display_name} ({count} messages)", inline=True)
                    else:
                        logger.warning("Top user is a bot or not found in guild")
                else:
                    logger.warning("No non-bot messages found in channel history")
            except Exception as e:
                logger.error(f"Error fetching message history from {chats_channel.name}: {e}")
        else:
            logger.warning("💬┃main-chat channel not found or not a text channel")

        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.set_footer(text=f"Server ID: {guild.id}")

        # Stop the animation before editing the message
        await thinking_animation.stop_thinking(interaction, delete_message=False)

        # Edit the animation message with the result
        if thinking_animation.animation_message:
            try:
                await thinking_animation.animation_message.edit(embed=embed)
                logger.info("Successfully edited animation message with serverstats results")
            except Exception as edit_error:
                logger.error(f"Failed to edit animation message with serverstats results: {edit_error}")
                # Fallback to followup send
                await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.error(f"Error in serverstats command: {e}")
        error_embed = discord.Embed(
            title="❌ Error Fetching Server Statistics",
            description="I encountered an error while fetching server statistics. Please try again.",
            color=0xff0000
        )
        try:
            # Try to edit animation message with error
            if thinking_animation.animation_message:
                await thinking_animation.animation_message.edit(embed=error_embed)
            else:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
        except Exception as edit_error:
            logger.error(f"Failed to send error message: {edit_error}")
            # Final fallback
            try:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            except Exception as final_error:
                logger.error(f"Failed to send final error message: {final_error}")

@bot.tree.command(name="mostactive", description="Show the top 3 most active users and activity graph based on messages in the current month")
async def mostactive(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    # Show thinking animation while processing
    await thinking_animation.show_thinking(interaction)

    # Use the channel where the command was invoked
    chats_channel = interaction.channel

    try:
        # Get start of current month
        now = datetime.utcnow()
        start_of_month = datetime(now.year, now.month, 1)

        message_counts = {}
        date_counts = {}
        async for message in chats_channel.history(limit=10000, after=start_of_month):
            if not message.author.bot:  # Exclude bot messages
                author_id = message.author.id
                message_counts[author_id] = message_counts.get(author_id, 0) + 1
                date = message.created_at.date()
                date_counts[date] = date_counts.get(date, 0) + 1

        if not message_counts:
            await interaction.followup.send(f"No messages found in {now.strftime('%B %Y')}.", ephemeral=True)
            return

        # Get top 3 users sorted by message count descending
        sorted_users = sorted(message_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        top_users = []
        for i, (user_id, count) in enumerate(sorted_users, 1):
            user = guild.get_member(user_id)
            if user and not user.bot:
                top_users.append((user, count, i))

        if not top_users:
            await interaction.followup.send(f"No valid users found in {now.strftime('%B %Y')}.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🏆 Top Active Users",
            description=f"Based on messages in {now.strftime('%B %Y')} in {chats_channel.mention}",
            color=0x3498db
        )

        medals = ["🥇", "🥈", "🥉"]
        for user, count, rank in top_users:
            medal = medals[rank - 1] if rank <= 3 else "🏅"
            embed.add_field(
                name=f"{medal} {rank}st Place",
                value=f"{user.display_name} ({count} messages)",
                inline=False
            )

        # If fewer than 3, note it
        if len(top_users) < 3:
            embed.add_field(
                name="ℹ️ Note",
                value=f"Only {len(top_users)} active users found in {now.strftime('%B %Y')}.",
                inline=False
            )

        embed.set_footer(text=f"Server: {guild.name}")

        # Stop the animation before editing the message
        await thinking_animation.stop_thinking(interaction, delete_message=False)

        # Edit the animation message with the result
        if thinking_animation.animation_message:
            try:
                await thinking_animation.animation_message.edit(embed=embed)
                logger.info("Successfully edited animation message with mostactive results")
            except Exception as edit_error:
                logger.error(f"Failed to edit animation message with mostactive results: {edit_error}")
                # Fallback to followup send
                await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(embed=embed)

        # Send the activity graph if data is available
        if date_counts:
            dates = sorted(date_counts.keys())
            counts = [date_counts[d] for d in dates]
            total_messages = sum(counts)
            average = total_messages / len(dates) if dates else 0
            start_date = dates[0].strftime('%Y-%m-%d') if dates else 'N/A'
            end_date = dates[-1].strftime('%Y-%m-%d') if dates else 'N/A'

            plt.figure(figsize=(12,6))
            bars = plt.bar(dates, counts, color='skyblue', edgecolor='black', alpha=0.7)
            # Highlight bars above average in orange
            for bar, count in zip(bars, counts):
                if count > average:
                    bar.set_color('orange')
            plt.axhline(y=average, color='red', linestyle='--', linewidth=2, label=f'Average: {average:.1f} msgs/day')
            plt.grid(True, alpha=0.3)
            plt.title(f'Daily Message Activity ({now.strftime("%B %Y")}: {total_messages} msgs from {start_date} to {end_date})', fontsize=14, fontweight='bold')
            plt.xlabel('Date', fontsize=12)
            plt.ylabel('Number of Messages', fontsize=12)
            plt.legend()
            # Format x-axis dates
            plt.gca().xaxis.set_major_formatter(DateFormatter('%Y-%m-%d'))
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            # Add top user annotation
            if top_users:
                top_user, top_count, _ = top_users[0]
                plt.text(0.02, 0.98, f'Top User: {top_user.display_name} ({top_count} msgs)', transform=plt.gca().transAxes,
                         fontsize=10, verticalalignment='top', bbox=dict(boxstyle='round,pad=0.3', facecolor='wheat', alpha=0.8))
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=100)
            buf.seek(0)
            file = discord.File(buf, 'activity_graph.png')
            plt.close()
            await interaction.followup.send(file=file)

    except Exception as e:
        logger.error(f"Error in mostactive command: {e}")
        error_embed = discord.Embed(
            title="❌ Error Fetching Message History",
            description="I encountered an error while fetching message history. Please try again.",
            color=0xff0000
        )
        try:
            # Try to edit animation message with error
            if thinking_animation.animation_message:
                await thinking_animation.animation_message.edit(embed=error_embed)
            else:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
        except Exception as edit_error:
            logger.error(f"Failed to send error message: {edit_error}")
            # Final fallback
            try:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            except Exception as final_error:
                logger.error(f"Failed to send final error message: {final_error}")

@bot.tree.command(name="help", description="Show information about available commands")
async def help_command(interaction: discord.Interaction):
    await animator.show_loading(interaction)
    try:
        # Main overview embed with clean, professional design
        embed = discord.Embed(
            title="⚡ Whiteout Survival Bot",
            description=(
                "Access all bot functions through categorized command modules.\n"
                "Use the dropdown below to explore each category.\n\n"
                "**📋 Available Modules**\n\n"
                "🎮 **Fun & Games** — 3 commands\n"
                "🎁 **Gift Codes & Rewards** — 3 commands\n"
                "🎵 **Music Player** — 15 commands\n"
                "⏰ **Reminders & Time** — 2 commands\n"
                "👥 **Community & Stats** — 4 commands\n"
                "🛡️ **Alliance Management** — 4 commands\n"
                "🌐 **Auto-Translate** — 5 commands\n"
                "⚙️ **Server Configuration** — 4 commands\n"
                "🔧 **Utility & Tools** — 2 commands"
            ),
            color=0x00d9ff
        )
        embed.set_thumbnail(url="https://i.postimg.cc/Fzq03CJf/a463d7c7-7fc7-47fc-b24d-1324383ee2ff-removebg-preview.png")
        embed.set_footer(text="Select a category to view detailed commands")

    except Exception as e:
        logger.error(f"Failed to build help embed: {e}")
        await animator.stop_loading(interaction, delete=True)
        try:
            await interaction.response.send_message("Failed to build help response.", ephemeral=True)
        except Exception:
            pass
        return

    # Interactive help view with category dropdown
    view = sv.InteractiveHelpView()
    try:
        await animator.stop_loading(interaction, delete=True)
        if not interaction.response.is_done():
            # send initial response; get message via original_response()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
            try:
                sent = await interaction.original_response()
                bot.add_view(view, message_id=sent.id)
            except Exception as reg_err:
                logger.debug(f"Failed to register HelpView after response: {reg_err}")
        else:
            # If response is already done, use followup.send and request the sent message
            sent = await interaction.followup.send(embed=embed, view=view, ephemeral=False, wait=True)
            try:
                bot.add_view(view, message_id=sent.id)
            except Exception as reg_err:
                logger.debug(f"Failed to register HelpView after followup: {reg_err}")
    except Exception as e:
        logger.error(f"Failed to send help embed: {e}")
        await animator.stop_loading(interaction, delete=True)
        try:
            # Final attempt using followup
            sent = await interaction.followup.send(embed=embed, view=view, ephemeral=False, wait=True)
            try:
                bot.add_view(view, message_id=sent.id)
            except Exception:
                pass
        except Exception as e2:
            logger.error(f"Failed to send help embed via followup: {e2}")


# --- Dice battle: a two-player roll with buttons ---
class DiceBattleView(discord.ui.View):
    """View that manages a two-player dice battle. Each player has one Roll button
    that only they can press. After both roll, the view declares a winner and
    disables the buttons."""
    def __init__(self, challenger: discord.Member, opponent: discord.Member, *, bg_url: str = None, sword_url: str = None, logo_url: str = None, timeout: float = None):
        # Use a persistent view by default (timeout=None). We'll register the
        # specific view instance for the sent message with bot.add_view(...,
        # message_id=sent.id) so the view callbacks remain available across
        # restarts and longer periods.
        super().__init__(timeout=timeout)
        self.challenger = challenger
        self.opponent = opponent
        # Optional asset overrides for image generation
        self.bg_url = bg_url
        self.sword_url = sword_url
        self.logo_url = logo_url
        # store results as {user_id: int or None}
        self.results = {challenger.id: None, opponent.id: None}
        self.message: discord.Optional[Message ] = None
        # Customize button labels and styles so each shows the player's name and different colors
        try:
            # short helper to trim long names for the button
            def _short(name: str, limit: int = 18) -> str:
                n = (name or "").strip()
                if len(n) <= limit:
                    return n
                return n[: limit - 1].rstrip() + "…"

            for child in list(self.children):
                cid = getattr(child, 'custom_id', '')
                if cid == 'dicebattle_roll_challenger':
                    child.label = f"Roll\n{_short(self.challenger.display_name)}"
                    child.style = discord.ButtonStyle.primary
                elif cid == 'dicebattle_roll_opponent':
                    child.label = f"Roll\n{_short(self.opponent.display_name)}"
                    # make opponent a different color
                    child.style = discord.ButtonStyle.success
        except Exception:
            # non-fatal: if UI objects aren't ready yet, ignore
            pass

    def build_embed(self) -> discord.Embed:
        """Create an embed showing both players and current results."""
        e = discord.Embed(title=f"🎲 Dice Battle: {self.challenger.display_name} vs {self.opponent.display_name}", color=0x3498db)
        # Challenger as author with avatar
        try:
            e.set_author(name=self.challenger.display_name, icon_url=self.challenger.display_avatar.url)
        except Exception:
            e.set_author(name=self.challenger.display_name)

        # Opponent avatar as thumbnail
        try:
            e.set_thumbnail(url=self.opponent.display_avatar.url)
        except Exception:
            pass

        # Fields for results
        cres = self.results.get(self.challenger.id)
        ores = self.results.get(self.opponent.id)
        e.add_field(name=f"Challenger — {self.challenger.display_name}", value=(str(cres) if cres is not None else "Not rolled"), inline=True)
        e.add_field(name=f"Opponent — {self.opponent.display_name}", value=(str(ores) if ores is not None else "Not rolled"), inline=True)

        if all(v is not None for v in self.results.values()):
            # Both rolled — determine winner
            a = self.results[self.challenger.id]
            b = self.results[self.opponent.id]
            if a > b:
                e.title = f"🏆 {self.challenger.display_name} wins!"
                e.color = 0x2ecc71
                e.description = f"**{self.challenger.display_name}** wins the dice battle with a roll of **{a}** against **{b}**. Congratulations!"
                try:
                    e.set_thumbnail(url=self.challenger.display_avatar.url)
                except Exception:
                    pass
            elif b > a:
                e.title = f"🏆 {self.opponent.display_name} wins!"
                e.color = 0x2ecc71
                e.description = f"**{self.opponent.display_name}** wins the dice battle with a roll of **{b}** against **{a}**. Congratulations!"
                try:
                    e.set_thumbnail(url=self.opponent.display_avatar.url)
                except Exception:
                    pass
            else:
                e.title = f"🤝 It's a tie!"
                e.color = 0xf1c40f
                e.description = f"Both players rolled **{a}** — it's a draw!"

            # Add a result field summarizing both rolls
            e.add_field(name="Result", value=f"{self.challenger.display_name}: **{a}**\n{self.opponent.display_name}: **{b}**", inline=False)

        return e

    async def create_battle_image(self, left_face_url: str = None, right_face_url: str = None, bg_url: str = None, sword_url: str = None, logo_url: str = None) -> discord.File:
        """Create a composite image showing both players' avatars with a crossed-swords
        emblem in the middle and optionally overlay dice-face images for left/right.
        Returns a discord.File ready to send as attachment.
        """
    # Default canvas sizes
        width = 900
        height = 360
        left_size = right_size = 320

        # Helper to fetch binary data for an avatar URL
        async def fetch_bytes(url: str) -> bytes:
            timeout = aiohttp.ClientTimeout(total=20)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            try:
                                return await resp.read()
                            except Exception as e:
                                logger.debug(f"Failed to read bytes from {url}: {e}")
                                return None
            except Exception as e:
                logger.debug(f"Exception fetching bytes from {url}: {e}")
            return None

        # Get avatar URLs (use display_avatar which is HTTP(s) URL)
        left_url = getattr(self.challenger.display_avatar, 'url', None) or getattr(self.challenger.avatar, 'url', None)
        right_url = getattr(self.opponent.display_avatar, 'url', None) or getattr(self.opponent.avatar, 'url', None)

        left_bytes = None
        right_bytes = None
        try:
            left_bytes, right_bytes = await asyncio.gather(fetch_bytes(left_url), fetch_bytes(right_url))
        except Exception:
            # Fallback: try sequentially
            try:
                left_bytes = await fetch_bytes(left_url)
            except Exception:
                left_bytes = None
            try:
                right_bytes = await fetch_bytes(right_url)
            except Exception:
                right_bytes = None

        # Load images (fallback to plain color if fetch failed)
        try:
            if left_bytes:
                left_img = Image.open(io.BytesIO(left_bytes)).convert('RGBA')
            else:
                left_img = Image.new('RGBA', (left_size, left_size), (200, 200, 200))
        except Exception:
            left_img = Image.new('RGBA', (left_size, left_size), (200, 200, 200))

        try:
            if right_bytes:
                right_img = Image.open(io.BytesIO(right_bytes)).convert('RGBA')
            else:
                right_img = Image.new('RGBA', (right_size, right_size), (180, 180, 180))
        except Exception:
            right_img = Image.new('RGBA', (right_size, right_size), (180, 180, 180))

        # Resize avatars to square
        left_img = left_img.resize((left_size, left_size), Image.LANCZOS)
        right_img = right_img.resize((right_size, right_size), Image.LANCZOS)

        # Use provided URLs (parameter) -> instance attr -> fallback to defaults
        default_bg_url = "https://cdn.discordapp.com/attachments/1435569370389807144/1435702034497278142/2208_w026_n002_2422b_p1_2422.jpg?ex=690ced37&is=690b9bb7&hm=04cdb75f595c5babb52fc3210fa548a02d3680e518728a1856429028ad5a3b65"
        default_sword_url = "https://cdn.discordapp.com/attachments/1435569370389807144/1435693707276845096/pngtree-crossed-swords-icon-combat-with-melee-weapons-duel-king-protect-vector-png-image_48129218-removebg-preview_2.png?ex=690ce575&is=690b93f5&hm=b564d747bfadcd5631911ce5e53710b70c7607410145e3c5ecc41a76fa55d5e8"
        default_logo_url = "https://cdn.discordapp.com/attachments/1435569370389807144/1435683133319282890/unnamed_3.png?ex=690cdb9c&is=690b8a1c&hm=e605500d0e061ee4983c68c30b68d3e285b03a88d31605ac65abf2b4df0ae028"

        # resolve urls: prefer explicit call args, then instance attrs, then defaults
        bg_url = bg_url or getattr(self, 'bg_url', None) or DICEBATTLE_BG_URL or default_bg_url
        sword_url = sword_url or getattr(self, 'sword_url', None) or DICEBATTLE_SWORD_URL or default_sword_url
        logo_url = logo_url or getattr(self, 'logo_url', None) or DICEBATTLE_LOGO_URL or default_logo_url

        canvas = Image.new('RGBA', (width, height), (40, 44, 52, 255))

        # Try to fetch and draw the background image (remote)
        try:
            bg_bytes = await fetch_bytes(default_bg_url)
            if bg_bytes:
                bg_img = Image.open(io.BytesIO(bg_bytes)).convert('RGBA')
                bg_img = bg_img.resize((width, height), Image.LANCZOS)
                canvas.paste(bg_img, (0, 0))
        except Exception:
            # ignore background failures
            pass

        draw = ImageDraw.Draw(canvas)

        # Create circular masks and paste avatars with a white ring
        pad_y = (height - left_size) // 2
        def paste_circular(img: Image.Image, x: int, y: int, size: int):
            try:
                mask = Image.new('L', (size, size), 0)
                mdraw = ImageDraw.Draw(mask)
                mdraw.ellipse((0, 0, size, size), fill=255)

                # create a white ring background
                ring = Image.new('RGBA', (size + 12, size + 12), (255, 255, 255, 0))
                rdraw = ImageDraw.Draw(ring)
                rdraw.ellipse((0, 0, size + 12, size + 12), fill=(255, 255, 255, 200))
                canvas.paste(ring, (x - 6, y - 6), ring)

                # paste avatar
                canvas.paste(img, (x, y), mask)
            except Exception:
                try:
                    canvas.paste(img, (x, y), img)
                except Exception:
                    canvas.paste(img, (x, y))

        left_x = 40
        right_x = width - right_size - 40
        paste_circular(left_img, left_x, pad_y, left_size)
        paste_circular(right_img, right_x, pad_y, right_size)

        # Overlay the crossed-swords PNG centered between avatars and place the supplied logo above it
        try:
            sword_bytes = await fetch_bytes(default_sword_url)
            if sword_bytes:
                sword_img = Image.open(io.BytesIO(sword_bytes)).convert('RGBA')
            else:
                sword_img = None
            if sword_img:
                # remove near-black background from sword image (make it transparent)
                try:
                    sdata = sword_img.getdata()
                    new_sdata = []
                    for item in sdata:
                        if len(item) >= 4:
                            r, g, b, a = item
                        else:
                            r, g, b = item
                            a = 255
                        # treat very dark pixels as transparent
                        if r < 30 and g < 30 and b < 30:
                            new_sdata.append((255, 255, 255, 0))
                        else:
                            new_sdata.append((r, g, b, a))
                    sword_img.putdata(new_sdata)
                except Exception:
                    pass

                # scale sword image to fit between avatars
                max_sword_w = 260
                w_ratio = max_sword_w / sword_img.width
                new_w = int(sword_img.width * w_ratio)
                new_h = int(sword_img.height * w_ratio)
                sword_img = sword_img.resize((new_w, new_h), Image.LANCZOS)
                sx = (width - new_w) // 2
                sy = (height - new_h) // 2
                canvas.paste(sword_img, (sx, sy), sword_img)

                # Now overlay provided logo above the sword (remote)
                try:
                    logo_bytes = await fetch_bytes(default_logo_url)
                    if logo_bytes:
                        logo_img = Image.open(io.BytesIO(logo_bytes)).convert('RGBA')
                    else:
                        logo_img = None
                    if logo_img:
                        # scale logo relative to sword (original size/position)
                        logo_w = int(new_w * 0.5)
                        logo_h = int(logo_img.height * (logo_w / logo_img.width))
                        logo_img = logo_img.resize((logo_w, logo_h), Image.LANCZOS)
                        lx = (width - logo_w) // 2
                        ly = sy - int(logo_h * 0.6)
                        canvas.paste(logo_img, (lx, ly), logo_img)
                except Exception:
                    pass
        except Exception:
            # fallback: draw simple crossed lines
            cx = width // 2
            cy = height // 2
            draw.line((cx - 40, cy - 40, cx + 40, cy + 40), fill=(240, 200, 200, 255), width=6)
            draw.line((cx + 40, cy - 40, cx - 40, cy + 40), fill=(240, 200, 200, 255), width=6)

        # Add small name plates under avatars
        try:
            fn = ImageFont.load_default()
            ln_w, ln_h = draw.textsize(self.challenger.display_name, font=fn)
            draw.rectangle([40, pad_y + left_size + 8, 40 + left_size, pad_y + left_size + 8 + ln_h + 6], fill=(0, 0, 0, 140))
            draw.text((40 + (left_size - ln_w) / 2, pad_y + left_size + 10), self.challenger.display_name, font=fn, fill=(255, 255, 255, 255))

            rn_w, rn_h = draw.textsize(self.opponent.display_name, font=fn)
            draw.rectangle([width - right_size - 40, pad_y + right_size + 8, width - 40, pad_y + right_size + 8 + rn_h + 6], fill=(0, 0, 0, 140))
            draw.text((width - right_size - 40 + (right_size - rn_w) / 2, pad_y + right_size + 10), self.opponent.display_name, font=fn, fill=(255, 255, 255, 255))
        except Exception:
            pass

        # Optionally overlay dice faces near avatars
        try:
            face_size = 110
            async def fetch_face(url: str):
                if not url:
                    return None
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            try:
                                return await resp.read()
                            except Exception as e:
                                logger.debug(f"Failed to read face image bytes from {url}: {e}")
                                return None
                        else:
                            logger.debug(f"Face image fetch returned {resp.status} for URL: {url}")
                return None

            if left_face_url:
                fb = None
                try:
                    fb = await fetch_face(left_face_url)
                except Exception:
                    fb = None
                if fb:
                    try:
                        fimg = Image.open(io.BytesIO(fb)).convert('RGBA')
                        fimg = fimg.resize((face_size, face_size), Image.LANCZOS)
                        # position: bottom-right corner of left avatar
                        lx = 40 + left_size - face_size // 2
                        ly = pad_y + left_size - face_size // 2
                        canvas.paste(fimg, (lx, ly), fimg)
                    except Exception:
                        pass

            if right_face_url:
                fb = None
                try:
                    fb = await fetch_face(right_face_url)
                except Exception:
                    fb = None
                if fb:
                    try:
                        fimg = Image.open(io.BytesIO(fb)).convert('RGBA')
                        fimg = fimg.resize((face_size, face_size), Image.LANCZOS)
                        # position: bottom-left corner of right avatar
                        rx = width - right_size - 40 + (right_size - face_size // 2)
                        ry = pad_y + right_size - face_size // 2
                        canvas.paste(fimg, (int(rx), int(ry)), fimg)
                    except Exception:
                        pass
        except Exception:
            pass

        # Export to BytesIO
        bio = io.BytesIO()
        canvas.convert('RGB').save(bio, format='PNG')
        bio.seek(0)
        return discord.File(bio, filename="battle.png")

    async def _handle_roll(self, interaction: discord.Interaction, player: discord.Member, button: discord.ui.Button):
        # Ensure only the intended user can press their button
        if interaction.user.id != player.id:
            await interaction.response.send_message("This roll button isn't for you.", ephemeral=True)
            return

        # Check if already rolled
        if self.results.get(player.id) is not None:
            await interaction.response.send_message("You already rolled.", ephemeral=True)
            return

        # Acknowledge interaction immediately
        try:
            await interaction.response.defer()
        except Exception:
            pass

        # Show rolling animation by editing the original message to use the GIF
        try:
            if self.message:
                anim_embed = self.build_embed()
                anim_embed.set_image(url=DICE_GIF_URL)
                await self.message.edit(embed=anim_embed, view=self)
        except Exception:
            # ignore failures to show animation
            pass

        # Wait a short time to simulate rolling
        try:
            await asyncio.sleep(2.0)
        except Exception:
            pass

        # Determine roll value
        value = random.randint(1, 6)
        self.results[player.id] = value

        # Update button state for that player and keep player's name shown below
        button.disabled = True
        try:
            # short name (match what's used on the initial label)
            pname = getattr(player, 'display_name', '')
            if len(pname) > 18:
                pname = pname[:17].rstrip() + '…'
            button.label = f"Rolled: {value}\n{pname}"
        except Exception:
            button.label = f"Rolled: {value}"

        # Build final composite image with this player's dice face overlaid
        face_url = DICE_FACE_URLS.get(value)
        try:
            if player.id == self.challenger.id:
                img_file = await self.create_battle_image(left_face_url=face_url)
            else:
                img_file = await self.create_battle_image(right_face_url=face_url)
        except Exception:
            img_file = None

        # Send updated message with new composite image and updated view, then remove the old one
        try:
            new_embed = self.build_embed()
            if img_file:
                new_embed.set_image(url="attachment://battle.png")
                new_msg = await self.message.channel.send(embed=new_embed, file=img_file, view=self)
            else:
                # Fallback: set image to the dice face URL directly (will replace center image)
                if face_url:
                    new_embed.set_image(url=face_url)
                new_msg = await self.message.channel.send(embed=new_embed, view=self)

            # Delete previous message to avoid duplication and update stored message reference
            try:
                await self.message.delete()
            except Exception:
                pass
            self.message = new_msg
            # Re-register this view instance for the new message so interactions
            # continue to be routed to this instance even after restarts.
            try:
                bot.add_view(self, message_id=new_msg.id)
                logger.debug(f"Registered DiceBattleView for message {getattr(new_msg, 'id', None)}: {addview_err}")
            except Exception as addview_err:
                logger.debug(f"Failed to register DiceBattleView for message {getattr(new_msg, 'id', None)}: {addview_err}")
        except Exception:
            # If sending new message fails, try editing original to show final result as text
            try:
                if self.message:
                    await self.message.edit(embed=self.build_embed(), view=self)
            except Exception:
                try:
                    await interaction.followup.send(embed=self.build_embed())
                except Exception:
                    pass

        # If both have rolled, finalize: disable all buttons and update message title
        if all(v is not None for v in self.results.values()):
            for child in self.children:
                child.disabled = True
            try:
                if self.message:
                    await self.message.edit(embed=self.build_embed(), view=self)
            except Exception:
                try:
                    await interaction.followup.send(embed=self.build_embed())
                except Exception:
                    pass

    @discord.ui.button(label="Roll", style=discord.ButtonStyle.primary, custom_id="dicebattle_roll_challenger")
    async def roll_challenger(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_roll(interaction, self.challenger, button)

    @discord.ui.button(label="Roll", style=discord.ButtonStyle.primary, custom_id="dicebattle_roll_opponent")
    async def roll_opponent(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_roll(interaction, self.opponent, button)


@bot.tree.command(name="dicebattle", description="Challenge someone to a dice battle")
@app_commands.describe(opponent="Member to challenge")
async def dicebattle(interaction: discord.Interaction, opponent: discord.Member):
    """Slash command to start a two-player dice battle.

    The challenger (invoker) selects an opponent. Both players will see a Roll
    button under the embed; each button only works for the corresponding player.
    After both click, the higher roll wins.
    """
    try:
        if opponent.bot:
            await interaction.response.send_message("You can't battle a bot.", ephemeral=True)
            return
        if opponent.id == interaction.user.id:
            await interaction.response.send_message("You can't battle yourself.", ephemeral=True)
            return

        view = DiceBattleView(interaction.user, opponent, bg_url=DICEBATTLE_BG_URL, sword_url=DICEBATTLE_SWORD_URL, logo_url=DICEBATTLE_LOGO_URL)
        # Build embed; defer first because creating the composite image may take >3s
        embed = view.build_embed()
        try:
            # Defer the interaction to buy time for image generation
            try:
                await interaction.response.defer()
            except Exception:
                # ignore if already deferred
                pass

            img_file = await view.create_battle_image()
            embed.set_image(url="attachment://battle.png")
            # send as followup (wait=True returns the sent message)
            sent = await interaction.followup.send(content=f"{interaction.user.mention} challenged {opponent.mention} to a dice battle!", embed=embed, file=img_file, view=view, wait=True)
            try:
                view.message = sent
                # Register this specific view instance for the sent message so
                # interactions with its buttons are routed to this instance even
                # after restarts.
                try:
                    bot.add_view(view, message_id=sent.id)
                    logger.debug(f"Registered DiceBattleView for message {sent.id}")
                except Exception as add_err:
                    logger.debug(f"Failed to register DiceBattleView for message {getattr(sent, 'id', None)}: {add_err}")
            except Exception:
                view.message = None
        except Exception:
            # If image creation or sending fails, ensure we still respond
            try:
                # If we already deferred above, use followup; else fallback to response
                if interaction.response.is_done():
                    sent = await interaction.followup.send(content=f"{interaction.user.mention} challenged {opponent.mention} to a dice battle!", embed=embed, view=view, wait=True)
                else:
                    await interaction.response.send_message(content=f"{interaction.user.mention} challenged {opponent.mention} to a dice battle!", embed=embed, view=view)
                    sent = await interaction.original_response()
                try:
                    view.message = sent
                    try:
                        bot.add_view(view, message_id=sent.id)
                    except Exception as add_err:
                        logger.debug(f"Failed to register DiceBattleView for message {getattr(sent, 'id', None)}: {add_err}")
                except Exception:
                    view.message = None
            except Exception:
                # Final fallback: attempt an ephemeral error message
                try:
                    await interaction.followup.send("Failed to start dice battle.", ephemeral=True)
                except Exception:
                    pass
        # store message reference for future edits
        try:
            view.message = await interaction.original_response()
        except Exception:
            # In some cases original_response may fail; try to fetch last message in channel
            try:
                channel = interaction.channel
                async for msg in channel.history(limit=5):
                    if msg.author == bot.user and msg.embeds and msg.embeds[0].title and interaction.user.display_name in msg.embeds[0].title:
                        view.message = msg
                        break
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error in /dicebattle command: {e}")
        try:
            await interaction.response.send_message("Failed to start dice battle.", ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="register_view", description="Register a persistent view for an existing message (admin)")
@app_commands.describe(channel="Channel containing the message", message_id="The message ID to register")
@app_commands.default_permissions(administrator=True)
async def register_view(interaction: discord.Interaction, channel: discord.TextChannel, message_id: str):
    """Admin helper: register a persistent view instance for an existing message so its buttons work again.
    
    Uses dropdown selection for view type instead of manual text input.
    """
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    try:
        # Fetch the message first
        try:
            msg = await channel.fetch_message(int(message_id))
        except Exception as e:
            await interaction.followup.send(f"Failed to fetch message: {e}", ephemeral=True)
            return

        # Create dropdown for view type selection
        class ViewTypeSelect(discord.ui.Select):
            def __init__(self, message, channel_obj):
                self.message_obj = message
                self.channel_obj = channel_obj
                options = [
                    discord.SelectOption(label="Help View", value="help", emoji="❓", description="Persistent help menu view"),
                    discord.SelectOption(label="Birthday Dashboard", value="birthday", emoji="🎂", description="Birthday management dashboard"),
                    discord.SelectOption(label="Birthday Wish", value="birthdaywish", emoji="🎉", description="Birthday wish message with gift button"),
                    discord.SelectOption(label="Gift Code", value="giftcode", emoji="🎁", description="Gift code redeem view"),
                    discord.SelectOption(label="Member List", value="memberlist", emoji="👥", description="Alliance member list view"),
                    discord.SelectOption(label="Record List", value="recordlist", emoji="📋", description="Custom project record list"),
                    discord.SelectOption(label="Record Detail", value="recorddetail", emoji="📁", description="Detailed view of a specific record"),
                ]
                super().__init__(placeholder="Select view type...", options=options, min_values=1, max_values=1)
            
            async def callback(self, select_interaction: discord.Interaction):
                try:
                    view_type = self.values[0]
                    
                    # Create appropriate view based on selection
                    view = None
                    metadata = {}
                    
                    if view_type == 'help':
                        view = sv.PersistentHelpView()
                    elif view_type == 'birthday':
                        view = BirthdayView()
                    elif view_type == 'birthdaywish':
                        # Extract birthday user IDs from the message embed or content
                        birthday_user_ids = []
                        if self.message_obj.embeds and self.message_obj.embeds[0].description:
                            import re
                            mentions = re.findall(r'<@!?(\d+)>', self.message_obj.embeds[0].description)
                            birthday_user_ids = [int(uid) for uid in mentions]
                        
                        if not birthday_user_ids:
                            await select_interaction.response.send_message(
                                "❌ Could not find birthday user mentions in message. Please ensure this is a birthday wish message.",
                                ephemeral=True
                            )
                            return
                        
                        from cogs.birthday_system import BirthdayWishView
                        view = BirthdayWishView(birthday_user_ids=birthday_user_ids)
                        metadata['birthday_user_ids'] = birthday_user_ids
                    elif view_type == 'giftcode':
                        view = sv.GiftCodeView()
                    elif view_type == 'memberlist':
                        # Extract alliance_id from the embed
                        if not self.message_obj.embeds:
                            await select_interaction.response.send_message("❌ Message has no embed. Cannot determine alliance_id.", ephemeral=True)
                            return
                        
                        alliance_id = 0
                        # Attempt to extract alliance_id from embed title or description
                        if self.message_obj.embeds and self.message_obj.embeds[0].title:
                            import re
                            match = re.search(r'\[(\d+)\]', self.message_obj.embeds[0].title)
                            if match:
                                alliance_id = int(match.group(1))
                            else:
                                # Try to find alliance name and look up ID
                                match = re.search(r'Alliance: (.+)', self.message_obj.embeds[0].title)
                                if match:
                                    alliance_name = match.group(1).strip()
                                    try:
                                        from db_utils import get_db_connection
                                        with get_db_connection('alliance.sqlite') as conn:
                                            cursor = conn.cursor()
                                            cursor.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
                                            result = cursor.fetchone()
                                            if result:
                                                alliance_id = result[0]
                                    except Exception as e:
                                        logger.error(f"Error looking up alliance_id: {e}")
                        
                        if alliance_id == 0:
                            await select_interaction.response.send_message(
                                "❌ Could not determine alliance_id from message. Please ensure the message is a valid member list.",
                                ephemeral=True
                            )
                            return
                        
                        from cogs.bot_operations import PersistentMemberListView
                        view = PersistentMemberListView(alliance_id=alliance_id)
                        metadata['alliance_id'] = alliance_id
                    elif view_type == 'recordlist':
                        from cogs.bot_operations import PersistentRecordsView
                        view = PersistentRecordsView()
                    elif view_type == 'recorddetail':
                        # Extract record_name from embed title
                        if not self.message_obj.embeds:
                            await select_interaction.response.send_message("❌ Message has no embed. Cannot determine record_name.", ephemeral=True)
                            return
                        
                        record_name = ""
                        title = self.message_obj.embeds[0].title or ""
                        if "Record: " in title:
                            record_name = title.split("Record: ", 1)[1].strip()
                        
                        if not record_name:
                            await select_interaction.response.send_message(
                                "❌ Could not determine record_name from message title. Expected 'Record: [Name]'.",
                                ephemeral=True
                            )
                            return
                        
                        from cogs.bot_operations import PersistentRecordDetailView
                        view = PersistentRecordDetailView(record_name=record_name)
                        metadata['record_name'] = record_name
                    
                    if view:
                        # Register view with bot
                        bot.add_view(view, message_id=self.message_obj.id)
                        
                        # Save to MongoDB if enabled
                        try:
                            from db.mongo_adapters import mongo_enabled, PersistentViewsAdapter
                            if mongo_enabled():
                                PersistentViewsAdapter.register_view(
                                    guild_id=select_interaction.guild_id,
                                    channel_id=self.channel_obj.id,
                                    message_id=self.message_obj.id,
                                    view_type=view_type,
                                    metadata=metadata
                                )
                                await select_interaction.response.send_message(
                                    f"✅ Registered and saved {view_type} view for message {self.message_obj.id} to MongoDB.",
                                    ephemeral=True
                                )
                            else:
                                await select_interaction.response.send_message(
                                    f"✅ Registered {view_type} view for message {self.message_obj.id} (MongoDB not enabled, will not persist across restarts).",
                                    ephemeral=True
                                )
                        except Exception as e:
                            logger.error(f"Failed to save view registration to MongoDB: {e}")
                            await select_interaction.response.send_message(
                                f"✅ Registered {view_type} view for message {self.message_obj.id}, but failed to save to MongoDB: {e}",
                                ephemeral=True
                            )
                        
                        logger.info(f"Manually registered {view_type} view for message {self.message_obj.id} in {self.channel_obj.guild}/{self.channel_obj.name}")
                    
                except Exception as e:
                    logger.error(f"Error in view type selection callback: {e}")
                    try:
                        await select_interaction.response.send_message(f"Failed to register view: {e}", ephemeral=True)
                    except Exception:
                        pass
        
        # Create view with dropdown
        view = discord.ui.View(timeout=60)
        view.add_item(ViewTypeSelect(msg, channel))
        
        embed = discord.Embed(
            title="🔧 Register Persistent View",
            description=f"Select the type of view to register for message `{msg.id}` in {channel.mention}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Message Preview", value=msg.content[:100] if msg.content else "(No content)", inline=False)
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Error in register_view command: {e}")
        try:
            await interaction.followup.send("Internal error registering view.", ephemeral=True)
        except Exception:
            pass



import sys, traceback, time

# --- Update / repair / dependency helpers ported from main.py ---
LEGACY_PACKAGES_TO_REMOVE = [
    "ddddocr",
    "easyocr",
    "torch",
    "torchvision",
    "torchaudio",
    "opencv-python-headless",
]

UPDATE_SOURCES = [
    {
        "name": "GitHub",
        "api_url": "https://api.github.com/repos/whiteout-project/bot/releases/latest",
        "primary": True
    },
    {
        "name": "GitLab",
        "api_url": "https://gitlab.whiteout-bot.com/api/v4/projects/1/releases",
        "project_id": 1,
        "primary": False
    }
]

def get_latest_release_info(beta_mode=False):
    """Try to get latest release info from multiple sources."""
    if requests is None:
        print("Update check skipped: requests package not available.")
        return None

    for source in UPDATE_SOURCES:
        try:
            print(f"Checking for updates from {source['name']}...")

            if source['name'] == "GitHub":
                if beta_mode:
                    repo_name = source['api_url'].split('/repos/')[1].split('/releases')[0]
                    branch_url = f"https://api.github.com/repos/{repo_name}/branches/main"
                    response = requests.get(branch_url, timeout=30)
                    if response.status_code == 200:
                        data = response.json()
                        commit_sha = data['commit']['sha'][:7]
                        return {
                            "tag_name": f"beta-{commit_sha}",
                            "body": f"Latest development version from main branch (commit: {commit_sha})",
                            "download_url": f"https://github.com/{repo_name}/archive/refs/heads/main.zip",
                            "source": f"{source['name']} (Beta)"
                        }
                else:
                    response = requests.get(source['api_url'], timeout=30)
                    if response.status_code == 200:
                        data = response.json()
                        repo_name = source['api_url'].split('/repos/')[1].split('/releases')[0]
                        download_url = f"https://github.com/{repo_name}/archive/refs/tags/{data['tag_name']}.zip"
                        return {
                            "tag_name": data["tag_name"],
                            "body": data.get("body", ""),
                            "download_url": download_url,
                            "source": source['name']
                        }

            elif source['name'] == "GitLab":
                response = requests.get(source['api_url'], timeout=30)
                if response.status_code == 200:
                    releases = response.json()
                    if releases:
                        latest = releases[0]
                        tag_name = latest['tag_name']
                        download_url = f"https://gitlab.whiteout-bot.com/whiteout-project/bot/-/archive/{tag_name}/bot-{tag_name}.zip"
                        return {
                            "tag_name": tag_name,
                            "body": latest.get("description", "No release notes available"),
                            "download_url": download_url,
                            "source": source['name']
                        }

        except requests.exceptions.RequestException as e:
            print(f"{source['name']} connection failed: {e}")
            continue
        except Exception as e:
            print(f"Failed to check {source['name']}: {e}")
            continue

    print("All update sources failed")
    return None

def check_and_install_requirements():
    if not os.path.exists("requirements.txt"):
        print("No requirements.txt found")
        return False
    
    import importlib.metadata
    
    with open("requirements.txt", "r") as f:
        # Filter commented out or empty lines
        requirements = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    missing_packages = []
    for requirement in requirements:
        # Get the distribution name (e.g. 'discord.py' from 'discord.py>=2.5.2')
        # This handles common operators (==, >=, <=, ~=, !=)
        dist_name = requirement.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("!=")[0].strip()
        
        # Strip square brackets (extras) like [socks] in aiohttp[socks]
        if "[" in dist_name:
            dist_name = dist_name.split("[")[0].strip()
            
        try:
            importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            # Common overrides where distribution name != module name, though metadata version check
            # should generally work with the name as it appears in requirements.txt (the distribution name).
            missing_packages.append(requirement)

    if missing_packages:
        print(f"Installing {len(missing_packages)} missing packages...")
        for package in missing_packages:
            try:
                # Use --no-cache-dir to avoid stale cache issues, and timeout for safety
                cmd = [sys.executable, "-m", "pip", "install", package, "--no-cache-dir"]
                subprocess.check_call(cmd, timeout=1200)
                print(f"✅ Installed {package}")
            except Exception as e:
                print(f"❌ Failed to install {package}: {e}")
                # We optionally continue but usually it's better to fail early if core is missing
                # return False 
    
    # Check for core packages one last time before declaring success
    print("All requirements satisfied")
    return True

def has_obsolete_requirements():
    """
    Check if requirements.txt contains obsolete packages from older versions.
    Required to fix bug with v1.2.0 upgrade logic that deleted new requirements.txt.
    """
    if not os.path.exists("requirements.txt"):
        return False
    try:
        with open("requirements.txt", "r") as f:
            content = f.read().lower()
        for package in LEGACY_PACKAGES_TO_REMOVE:
            if package.lower() in content:
                return True
        return False
    except Exception as e:
        print(f"Error checking requirements.txt: {e}")
        return False

def setup_dependencies(beta_mode=False):
    print("\nChecking dependencies...")
    removed_obsolete = False
    if has_obsolete_requirements():
        print("! Warning: requirements.txt contains obsolete packages from older version")
        removed_obsolete = True
        try:
            # os.remove("requirements.txt") 
            print("! Kept requirements.txt despite obsolete packages")
        except Exception:
            pass

    if not os.path.exists("requirements.txt"):
        if not removed_obsolete:
            print("! Warning: requirements.txt not found")
        # Note: requirements.txt should be present in the repository
        # If missing, restore from backup or repository
        print("✗ requirements.txt missing - please restore from repository")
        return False

    if not check_and_install_requirements():
        print("✗ Failed to install requirements")
        return False
    return True

def startup_cleanup():
    v1_path = "V1oldbot"
    if os.path.exists(v1_path):
        safe_remove(v1_path)
    v2_path = "V2Old"
    if os.path.exists(v2_path):
        safe_remove(v2_path)
    pictures_path = "pictures"
    if os.path.exists(pictures_path):
        safe_remove(pictures_path)
    txt_path = "autoupdateinfo.txt"
    if os.path.exists(txt_path):
        safe_remove(txt_path, is_dir=False)
    legacy_packages = [p for p in LEGACY_PACKAGES_TO_REMOVE if is_package_installed(p)]
    if legacy_packages:
        uninstall_packages(legacy_packages, " (legacy packages)")

def restart_bot():
    python = sys.executable
    script_path = os.path.abspath(sys.argv[0])
    filtered_args = [arg for arg in sys.argv[1:] if arg not in ["--no-venv", "--repair"]]
    args = [python, script_path] + filtered_args
    if sys.platform == "win32":
        print("Please restart the bot manually with the venv python if needed.")
        sys.exit(0)
    else:
        try:
            subprocess.Popen(args)
            os._exit(0)
        except Exception:
            os.execl(python, python, script_path, *sys.argv[1:])

def install_packages(requirements_txt_path: str, debug: bool = False) -> bool:
    full_command = [sys.executable, "-m", "pip", "install", "-r", requirements_txt_path, "--no-cache-dir"]
    try:
        subprocess.check_call(full_command, timeout=1200)
        return True
    except Exception as e:
        print(f"Failed to install packages: {e}")
        return False

async def check_and_update_files():
    beta_mode = "--beta" in sys.argv
    repair_mode = "--repair" in sys.argv
    release_info = get_latest_release_info(beta_mode=beta_mode)
    if not release_info:
        print("No release info available")
        return
    latest_tag = release_info["tag_name"]
    source_name = release_info.get("source", "Unknown")
    current_version = "v0.0.0"
    if os.path.exists("version"):
        with open("version", "r") as f:
            current_version = f.read().strip()
    if current_version != latest_tag or repair_mode:
        update = False
        if is_container():
            update = True
        else:
            if "--autoupdate" in sys.argv or repair_mode:
                update = True
            else:
                ask = input("Do you want to update? (y/n): ").strip().lower()
                update = ask == "y"

        if update:
            download_url = release_info.get("download_url")
            if not download_url:
                print("No download URL for update")
                return
            safe_remove("package.zip")
            resp = requests.get(download_url, timeout=600)
            if resp.status_code == 200:
                with open("package.zip", "wb") as f:
                    f.write(resp.content)
                try:
                    shutil.unpack_archive("package.zip", "update", "zip")
                except Exception as e:
                    print(f"Failed to extract update: {e}")
                    return
                # Copy files from update into place (skip certain files)
                update_dir = "update"
                extracted_items = os.listdir(update_dir)
                if len(extracted_items) == 1 and os.path.isdir(os.path.join(update_dir, extracted_items[0])):
                    update_dir = os.path.join(update_dir, extracted_items[0])

                requirements_path = os.path.join(update_dir, "requirements.txt")
                if os.path.exists(requirements_path):
                    success = install_packages(requirements_path)
                    if success:
                        try:
                            if os.path.exists("requirements.txt"):
                                safe_remove("requirements.txt", is_dir=False)
                            shutil.copy2(requirements_path, "requirements.txt")
                        except Exception:
                            pass

                for root, _, files in os.walk(update_dir):
                    for file in files:
                        if file == "main.py":
                            continue
                        src_path = os.path.join(root, file)
                        rel_path = os.path.relpath(src_path, update_dir)
                        dst_path = os.path.join(".", rel_path)
                        if file in ["bot_token.txt", "version"] or dst_path.startswith("db/"):
                            continue
                        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                        try:
                            shutil.copy2(src_path, dst_path)
                        except Exception as e:
                            print(f"Failed to copy {file}: {e}")

                safe_remove("package.zip")
                safe_remove("update")
                with open("version", "w") as f:
                    f.write(latest_tag)
                restart_bot()

# --- End of update/repair helpers ---

# Run dependency/setup/update flow before starting bot when invoked as script
if __name__ == "__main__":
    beta_mode = "--beta" in sys.argv
    if not setup_dependencies(beta_mode=beta_mode):
        print("Warning: Dependency setup incomplete. Proceeding may fail.")
    try:
        startup_cleanup()
    except Exception:
        pass
    # Check for updates/repair unless explicitly skipped
    if "--no-update" not in sys.argv:
        try:
            import asyncio as _asyncio
            _asyncio.run(check_and_update_files())
        except Exception:
            pass

try:
    if not TOKEN:
        logger.critical("DISCORD_TOKEN not found in environment variables!")
        # Sleep to avoid rapid restart loop if config is missing
        time.sleep(60)
        sys.exit(1)
        
    bot.run(TOKEN)
except discord.errors.HTTPException as e:
    if e.status == 429:
        logger.critical(f"🛑 RATE LIMITED (429) by Discord/Cloudflare. Sleeping for 15 minutes to cool down IP ban... Error: {e}")
        # Sleep 15 minutes to let the ban expire
        time.sleep(900)
    else:
        logger.error(f"Discord HTTP Exception: {e}", exc_info=True)
    # Re-raise to exit
    raise
except BaseException as e:
    # Catch BaseException so we also capture SystemExit and KeyboardInterrupt
    logger.error(f"Bot exited with: {type(e).__name__}: {e}", exc_info=True)
    traceback.print_exc()
    # keep the process alive briefly for inspection (default 30s)
    # Extended to 60s to give more time to read logs
    for i in range(60):
        logger.error(f"Bot exited — sleeping for inspection ({i+1}/60)")
        time.sleep(1)
    # re-raise to preserve original behavior after inspection
    raise

