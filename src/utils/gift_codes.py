from typing import Union
import aiohttp
import asyncio
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import logging
import dateutil.parser
import discord

logger = logging.getLogger(__name__)

def build_codes_embed(codes):
    """Builds a Discord embed for a list of gift codes."""
    if not codes:
        return discord.Embed(
            title="🎁 Gift Codes",
            description="No active codes found.",
            color=discord.Color.red()
        )
    
    embed = discord.Embed(
        title="🎁 Active Gift Codes",
        description=f"Found {len(codes)} active codes!",
        color=discord.Color.green()
    )
    
    for code in codes:
        name = code.get('code', 'Unknown')
        rewards = code.get('rewards', 'Unknown Rewards')
        expiry = code.get('expiry', 'Unknown Expiry')
        
        embed.add_field(
            name=f"🏷️ {name}",
            value=f"🎁 {rewards}\n⏳ Expires: {expiry}",
            inline=False
        )
        
    embed.set_footer(text="Use the buttons below to copy codes or refresh the list.")
    return embed

class WosToolsScraper:
    """Scraper for https://wostools.net/api/gift-codes — fast JSON API (primary source)."""

    API_URL = "https://wostools.net/api/gift-codes"

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self.cache_data = None
        self.last_fetched = None
        self.cache_duration = timedelta(seconds=10)  # 10s cache for fast detection

    async def fetch_gift_codes(self):
        """
        Fetch active gift codes from wostools.net JSON API.
        Returns list of code dicts with keys: code, rewards, expiry, is_active
        """
        # Return cached result if still valid
        if self.cache_data is not None and self.last_fetched:
            if datetime.now() - self.last_fetched < self.cache_duration:
                logger.debug("WosTools: returning cached gift codes")
                return self.cache_data

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.API_URL,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"WosTools API returned status {response.status}")
                        return self.cache_data or []

                    data = await response.json(content_type=None)

                    if not data.get('success'):
                        logger.warning("WosTools API returned success=false")
                        return self.cache_data or []

                    codes = []
                    for item in data.get('codes', []):
                        status = str(item.get('status', '')).strip().lower()
                        if status != 'active':
                            continue
                        code_str = item.get('code', '').strip()
                        if not code_str:
                            continue
                        date_added = item.get('dateAdded', '')
                        label = item.get('label') or ''
                        rewards = (
                            item.get('rewards')
                            or item.get('reward')
                            or item.get('rewardText')
                            or item.get('description')
                            or label
                            or 'Rewards not specified'
                        )
                        expiry = (
                            item.get('expiry')
                            or item.get('expires')
                            or item.get('expiresAt')
                            or item.get('expiration')
                            or item.get('expirationDate')
                            or 'Unknown'
                        )
                        codes.append({
                            'code': code_str,
                            'description': label,
                            'rewards': str(rewards).strip() if rewards else 'Rewards not specified',
                            'expiry': str(expiry).strip() if expiry else 'Unknown',
                            'is_active': True,
                            'status': status,
                            'source': 'wostools',
                            'date_added': date_added,
                        })

                    logger.info(f"WosTools API: fetched {len(codes)} active codes")
                    self.cache_data = codes
                    self.last_fetched = datetime.now()
                    return codes

        except asyncio.TimeoutError:
            logger.warning("WosTools API: request timed out")
            return self.cache_data or []
        except Exception as e:
            logger.error(f"WosTools API error: {e}")
            return self.cache_data or []


class WosGiftCodesRssScraper:
    """
    RSS feed scraper for https://wosgiftcodes.com/rss.php — lightweight, fast (secondary source).
    """

    RSS_URL = "https://wosgiftcodes.com/rss.php"

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self.cache_data = None
        self.last_fetched = None
        self.cache_duration = timedelta(seconds=30)

    async def fetch_gift_codes(self):
        """Fetch codes from wosgiftcodes.com RSS feed."""
        if self.cache_data is not None and self.last_fetched:
            if datetime.now() - self.last_fetched < self.cache_duration:
                logger.debug("WosGiftCodesRSS: returning cached gift codes")
                return self.cache_data

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.RSS_URL,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"WosGiftCodes RSS returned status {response.status}")
                        return self.cache_data or []

                    text = await response.text()
                    codes = self._parse_rss(text)
                    logger.info(f"WosGiftCodes RSS: fetched {len(codes)} active codes")
                    self.cache_data = codes
                    self.last_fetched = datetime.now()
                    return codes

        except asyncio.TimeoutError:
            logger.warning("WosGiftCodes RSS: request timed out")
            return self.cache_data or []
        except Exception as e:
            logger.error(f"WosGiftCodes RSS error: {e}")
            return self.cache_data or []

    def _parse_rss(self, xml_text: str):
        """Parse RSS XML and extract active gift codes."""
        codes = []
        try:
            soup = BeautifulSoup(xml_text, 'xml')
            items = soup.find_all('item')
            for item in items:
                title_tag = item.find('title')
                desc_tag = item.find('description')
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                desc = desc_tag.get_text(strip=True) if desc_tag else ''

                # The title is typically the gift code itself
                if not title or len(title) < 3:
                    continue
                # Skip generic header titles
                if title.lower() in ('active codes', 'expired codes', 'gift codes', 'whiteout survival gift codes'):
                    continue
                # Skip titles with spaces in first 6 chars (likely sentences, not codes)
                if ' ' in title[:6]:
                    continue

                codes.append({
                    'code': title.strip(),
                    'description': desc,
                    'rewards': desc or 'Rewards not specified',
                    'expiry': 'Unknown',
                    'is_active': True,
                    'source': 'wosgiftcodes_rss',
                    'date_added': '',
                })
        except Exception as e:
            logger.error(f"WosGiftCodes RSS parse error: {e}")
        return codes


class GiftCodeScraper:
    """HTML scraper for https://wosgiftcodes.com/ — tertiary fallback source."""

    def __init__(self):
        self.url = "https://wosgiftcodes.com/"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.cache_data = None
        self.last_fetched = None
        self.cache_duration = timedelta(minutes=2)
    
    async def fetch_gift_codes(self):
        """
        Fetch and parse gift codes from wosgiftcodes.com
        Returns a dict with active and expired codes
        """
        # Return cached result if valid
        if self.cache_data and self.last_fetched:
            if datetime.now() - self.last_fetched < self.cache_duration:
                logger.debug("Returning cached gift codes")
                return self.cache_data

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status != 200:
                        logger.error(f"Failed to fetch gift codes. Status: {response.status}")
                        return {
                            'active_codes': [],
                            'expired_codes': [],
                            'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }

                    html = await response.text()
                    result = self.parse_gift_codes(html)
                    if result is None:
                        return {
                            'active_codes': [],
                            'expired_codes': [],
                            'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                    
                    self.cache_data = result
                    self.last_fetched = datetime.now()
                    return result

        except aiohttp.ClientTimeout:
            logger.error("Timeout while fetching gift codes")
            return {
                'active_codes': [],
                'expired_codes': [],
                'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            logger.error(f"Error fetching gift codes: {str(e)}")
            return {
                'active_codes': [],
                'expired_codes': [],
                'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
    
    def parse_gift_codes(self, html):
        """Parse HTML content to extract gift codes."""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            active_codes = []
            expired_codes = []
            
            # Method 1: Look for structured sections
            active_section = soup.find('h2', text=re.compile(r'Active Codes', re.I))
            if not active_section:
                tag = soup.find(string=re.compile(r'Active Codes', re.I))
                if tag:
                    active_section = getattr(tag, 'parent', None)
            if active_section:
                active_codes = self.extract_codes_from_section(active_section, is_active=True)
            
            expired_section = soup.find('h2', text=re.compile(r'Expired Codes', re.I))
            if expired_section:
                expired_codes = self.extract_codes_from_section(expired_section, is_active=False)
            
            # Method 2: Fallback
            if not active_codes and not expired_codes:
                logger.info("No structured sections found, trying text parsing fallback")
                active_codes, expired_codes = self.parse_text_content(html)
            
            # Method 3: table-responsive fallback
            if not active_codes:
                soup_div = soup.find('div', class_='table-responsive')
                if soup_div:
                    table = soup_div.find('table')
                    if table:
                        active_codes = self.extract_from_table(table, is_active=True)
            
            return {
                'active_codes': active_codes,
                'expired_codes': expired_codes,
                'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        
        except Exception as e:
            logger.error(f"Error parsing gift codes: {str(e)}")
            return None
    
    def extract_codes_from_section(self, section_header, is_active=True):
        codes = []
        try:
            current = section_header.find_next_sibling()
            while current:
                if current.name == 'table':
                    codes.extend(self.extract_from_table(current, is_active))
                    break
                elif current.name in ['div', 'section'] and any(keyword in current.get_text().lower() for keyword in ['code', 'gems', 'rewards']):
                    codes.extend(self.extract_from_div(current, is_active))
                elif current.name in ['h2', 'h3'] and current != section_header:
                    break
                current = current.find_next_sibling()
            
            if not codes:
                next_sibling = section_header.find_next_sibling()
                if next_sibling and next_sibling.name == 'div' and 'table-responsive' in next_sibling.get('class', []):
                    table = next_sibling.find('table')
                    if table:
                        codes.extend(self.extract_from_table(table, is_active))
                else:
                    codes = self.extract_from_text_content(section_header, is_active)
        except Exception as e:
            logger.error(f"Error extracting codes from section: {str(e)}")
        return codes
    
    def extract_from_table(self, table, is_active):
        codes = []
        rows = table.find_all('tr')[1:]
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 3:
                code = cells[0].get_text(strip=True)
                description = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                rewards = cells[2].get_text(strip=True) if len(cells) > 2 else description
                expiry = cells[3].get_text(strip=True) if len(cells) > 3 else ("Unknown" if is_active else "Expired")
                if code and code.upper() not in ['CODE', 'DESCRIPTION', 'REWARDS', 'EXPIRES']:
                    codes.append({
                        'code': code,
                        'description': description,
                        'rewards': rewards,
                        'expiry': expiry,
                        'is_active': is_active
                    })
        return codes
    
    def extract_from_div(self, div, is_active):
        codes = []
        text = div.get_text()
        code_matches = re.findall(r'([a-zA-Z0-9]{4,15})', text)
        for code in code_matches:
            code_context = self.find_code_context(text, code)
            codes.append({
                'code': code,
                'description': code_context.get('description', ''),
                'rewards': code_context.get('rewards', ''),
                'expiry': code_context.get('expiry', 'Unknown' if is_active else 'Expired'),
                'is_active': is_active
            })
        return codes
    
    def extract_from_text_content(self, section_header, is_active):
        codes = []
        section_text = ""
        current = section_header
        for _ in range(10):
            current = current.find_next_sibling()
            if not current or (current.name in ['h2', 'h3'] and current != section_header):
                break
            section_text += current.get_text() + " "
        
        code_pattern = r'([a-zA-Z0-9]{4,15})\s+([^0-9\n]+?)(?:\s+([\d-]+\s+[\d:]|\w+\s+\d+))?'
        matches = re.finditer(code_pattern, section_text)
        for match in matches:
            code = match.group(1)
            if code not in [c['code'] for c in codes]:
                rewards = match.group(2).strip() if match.group(2) else ''
                expiry = match.group(3) if match.group(3) else ('Unknown' if is_active else 'Expired')
                codes.append({
                    'code': code,
                    'description': '',
                    'rewards': rewards,
                    'expiry': expiry,
                    'is_active': is_active
                })
        return codes
    
    def find_code_context(self, text, code):
        context = {'description': '', 'rewards': '', 'expiry': 'Unknown'}
        code_pos = text.find(code)
        if code_pos == -1:
            return context
        after = text[code_pos+len(code):code_pos+len(code)+200]
        reward_keywords = ['gems', 'shards', 'speed', 'vip', 'meat', 'wood', 'coal', 'iron', 'xp', 'hero', 'chest']
        rewards = []
        for keyword in reward_keywords:
            if keyword.lower() in after.lower():
                reward_match = re.search(rf'(\d+[kKmM]?\s+{keyword})', after, re.IGNORECASE)
                if reward_match:
                    rewards.append(reward_match.group(1))
        context['rewards'] = ', '.join(rewards) if rewards else after.split('\n')[0][:100]
        date_pattern = r'(\d{4}-\d{2}-\d{2}|\w+ \d{1,2}, \d{4})'
        date_match = re.search(date_pattern, after)
        if date_match:
            context['expiry'] = date_match.group(1)
        return context
    
    def parse_text_content(self, html):
        active_codes = []
        expired_codes = []
        try:
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.get_text()
            active_match = re.search(r'Active Codes?(.*?)(?=Expired Codes?|$)', text, re.IGNORECASE | re.DOTALL)
            if active_match:
                active_codes = self.extract_codes_from_text(active_match.group(1), is_active=True)
            expired_match = re.search(r'Expired Codes?(.*?)(?=Final Results|$)', text, re.IGNORECASE | re.DOTALL)
            if expired_match:
                expired_codes = self.extract_codes_from_text(expired_match.group(1), is_active=False)
        except Exception as e:
            logger.error(f"Error in text content parsing: {str(e)}")
        return active_codes, expired_codes
    
    def extract_codes_from_text(self, text, is_active=True):
        codes = []
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        i = 0
        while i < len(lines):
            line = lines[i]
            code_match = re.match(r'^([a-zA-Z0-9]{4,20})\s+(.+)', line)
            if code_match:
                code = code_match.group(1)
                rest_of_line = code_match.group(2)
                if code.upper() in ['CODE', 'DESCRIPTION', 'REWARDS', 'EXPIRES', 'FINAL', 'RESULTS']:
                    i += 1
                    continue
                rewards = ""
                expiry = "Unknown" if is_active else "Expired"
                full_text = rest_of_line
                for j in range(i + 1, min(i + 3, len(lines))):
                    if lines[j] and not re.match(r'^[a-zA-Z0-9]{4,20}\s', lines[j]):
                        full_text += " " + lines[j]
                    else:
                        break
                date_match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', full_text)
                if date_match:
                    expiry = date_match.group(1)
                    rewards = re.sub(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}', '', full_text).strip()
                else:
                    rewards = full_text
                rewards = re.sub(r'\s+', ' ', rewards).strip()
                codes.append({
                    'code': code,
                    'description': '',
                    'rewards': rewards,
                    'expiry': expiry,
                    'is_active': is_active
                })
            i += 1
        return codes


# Global instances
gift_code_scraper = GiftCodeScraper()
wostools_scraper = WosToolsScraper()
wosgiftcodes_rss_scraper = WosGiftCodesRssScraper()

async def get_active_gift_codes():
    """
    Public function to get active gift codes.
    Aggregates from multiple sources concurrently:
      1. wostools.net JSON API        (primary — fast, authoritative, 10s cache)
      2. wosgiftcodes.com RSS feed    (secondary — lightweight XML, 30s cache)
      3. wosgiftcodes.com HTML scrape (tertiary fallback — 2min cache)
    Results are deduplicated by code string (case-insensitive).
    Returns list of active code dicts, or empty list on error.
    """
    # Run all three scrapers concurrently
    wostools_task = asyncio.create_task(wostools_scraper.fetch_gift_codes())
    rss_task = asyncio.create_task(wosgiftcodes_rss_scraper.fetch_gift_codes())
    html_task = asyncio.create_task(gift_code_scraper.fetch_gift_codes())

    wostools_codes, rss_codes, wosgift_result = await asyncio.gather(
        wostools_task, rss_task, html_task, return_exceptions=True
    )

    # Handle exceptions from gather
    if isinstance(wostools_codes, Exception):
        logger.error(f"WosTools scraper exception: {wostools_codes}")
        wostools_codes = []
    if isinstance(rss_codes, Exception):
        logger.error(f"WosGiftCodes RSS scraper exception: {rss_codes}")
        rss_codes = []
    if isinstance(wosgift_result, Exception):
        logger.error(f"WosGiftCodes HTML scraper exception: {wosgift_result}")
        wosgift_result = None

    # Extract active codes from wosgiftcodes.com HTML result
    wosgift_codes = []
    if wosgift_result and isinstance(wosgift_result, dict):
        now = datetime.now()
        for code in wosgift_result.get('active_codes', []):
            expiry_str = code.get('expiry', 'Unknown')
            try:
                expiry_date = dateutil.parser.parse(expiry_str, fuzzy=True)
                if expiry_date > now:
                    wosgift_codes.append(code)
            except Exception:
                wosgift_codes.append(code)  # Assume active if unparseable

    # Merge: wostools (primary) → RSS → HTML, deduplicated by uppercase code.
    # If WosTools has an active list, treat it as the authority and only use
    # secondary sources to enrich matching codes. This prevents expired or
    # unverified fallback entries from appearing as active in the dashboard.
    merged_dict = {}
    wostools_keys = {
        code_dict.get('code', '').strip().upper()
        for code_dict in (wostools_codes or [])
        if code_dict.get('code')
    }

    def _merge_code(code_dict):
        key = code_dict.get('code', '').strip().upper()
        if not key:
            return
        if key == 'WOSTOOLSDOTNET':
            return
        if key not in merged_dict:
            merged_dict[key] = code_dict
        else:
            # Update existing if new dict has better details
            existing = merged_dict[key]
            new_rewards = code_dict.get('rewards', '').strip()
            if new_rewards and new_rewards.lower() != 'rewards not specified' and (existing.get('rewards', '').lower() == 'rewards not specified' or not existing.get('rewards')):
                existing['rewards'] = new_rewards
                
            new_expiry = code_dict.get('expiry', '').strip()
            if new_expiry and new_expiry.lower() not in ['unknown', 'expired'] and (existing.get('expiry', '').lower() in ['unknown', 'expired'] or not existing.get('expiry')):
                existing['expiry'] = new_expiry

    for code_dict in (wostools_codes or []):
        _merge_code(code_dict)

    for code_dict in (rss_codes or []):
        key = code_dict.get('code', '').strip().upper()
        if not wostools_keys or key in wostools_keys:
            _merge_code(code_dict)

    for code_dict in wosgift_codes:
        key = code_dict.get('code', '').strip().upper()
        if not wostools_keys or key in wostools_keys:
            _merge_code(code_dict)

    merged = list(merged_dict.values())

    merged = [
        code
        for code in merged
        if str(code.get('status') or code.get('validation_status') or '').strip().lower() == 'active'
    ]

    if merged:
        logger.info(
            f"get_active_gift_codes: {len(merged)} unique active codes "
            f"({len(wostools_codes or [])} from WosTools, "
            f"{len(rss_codes or [])} from RSS, "
            f"{len(wosgift_codes)} from HTML)"
        )
    else:
        logger.warning("get_active_gift_codes: no active codes found from any source")

    return merged if merged else []

async def get_all_gift_codes():
    """
    Public function to get all gift codes (active and expired)
    """
    return await gift_code_scraper.fetch_gift_codes()
