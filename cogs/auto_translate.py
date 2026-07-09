import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import logging
import os
from typing import Optional, List
from datetime import datetime
import deepl

try:
    from db.mongo_adapters import AutoTranslateAdapter, mongo_enabled
except ImportError:
    AutoTranslateAdapter = None
    mongo_enabled = lambda: False

logger = logging.getLogger(__name__)

# DeepL supported languages for dropdowns
SUPPORTED_LANGUAGES = {
    'ar': '🇸🇦 Arabic',
    'bg': '🇧🇬 Bulgarian',
    'cs': '🇨🇿 Czech',
    'da': '🇩🇰 Danish',
    'de': '🇩🇪 German',
    'el': '🇬🇷 Greek',
    'en': '🇬🇧 English',
    'es': '🇪🇸 Spanish',
    'et': '🇪🇪 Estonian',
    'fi': '🇫🇮 Finnish',
    'fr': '🇫🇷 French',
    'hu': '🇭🇺 Hungarian',
    'id': '🇮🇩 Indonesian',
    'it': '🇮🇹 Italian',
    'ja': '🇯🇵 Japanese',
    'ko': '🇰🇷 Korean',
    'lt': '🇱🇹 Lithuanian',
    'lv': '🇱🇻 Latvian',
    'nb': '🇳🇴 Norwegian',
    'nl': '🇳🇱 Dutch',
    'pl': '🇵🇱 Polish',
    'pt': '🇵🇹 Portuguese',
    'ro': '🇷🇴 Romanian',
    'ru': '🇷🇺 Russian',
    'sk': '🇸🇰 Slovak',
    'sl': '🇸🇮 Slovenian',
    'sv': '🇸🇪 Swedish',
    'tr': '🇹🇷 Turkish',
    'uk': '🇺🇦 Ukrainian',
    'zh': '🇨🇳 Chinese',
    'hi': '🇮🇳 Hindi',
}


class DeepLTranslator:
    """DeepL API client for translation"""
    
    def __init__(self, api_key: str):
        self.translator = deepl.Translator(api_key)
        self._supported_languages = None
    
    async def translate(self, text: str, target_lang: str, source_lang: Optional[str] = None) -> str:
        """Translate text using DeepL API"""
        try:
            # Normalize language codes for DeepL
            # DeepL uses EN-US, EN-GB, PT-BR, PT-PT, etc.
            target_normalized = target_lang.upper()
            if target_normalized == 'EN':
                target_normalized = 'EN-US'
            elif target_normalized == 'PT':
                target_normalized = 'PT-PT'
            
            source_normalized = None
            if source_lang:
                source_normalized = source_lang.upper()
                if source_normalized == 'EN':
                    source_normalized = 'EN-US'
                elif source_normalized == 'PT':
                    source_normalized = 'PT-PT'
            
            logger.info(f"DeepL translate: '{text[:50]}' from {source_normalized or 'auto'} to {target_normalized}")
            
            result = await asyncio.to_thread(
                self.translator.translate_text,
                text,
                target_lang=target_normalized,
                source_lang=source_normalized
            )
            
            logger.info(f"DeepL result: '{result.text[:50]}' (detected source: {result.detected_source_lang})")
            return result.text
        except Exception as e:
            logger.warning(f"DeepL Translation error (quota?): {e}. Falling back to GoogleTranslator.")
            try:
                from deep_translator import GoogleTranslator
                
                # Google translator uses ISO 639-1 base codes
                g_target = target_lang.lower()
                g_source = source_lang.lower() if source_lang else 'auto'
                
                # deep_translator uses 'zh-CN' for Chinese
                if g_target == 'zh':
                    g_target = 'zh-CN'
                if g_source == 'zh':
                    g_source = 'zh-CN'
                
                gt = GoogleTranslator(source=g_source, target=g_target)
                result_text = await asyncio.to_thread(gt.translate, text)
                
                logger.info(f"Google fallback result: '{result_text[:50]}'")
                return result_text
            except Exception as e2:
                logger.error(f"Google Fallback translation error: {e2}")
                return text
    
    async def detect_language(self, text: str) -> str:
        """Detect language by translating to English"""
        try:
            result = await asyncio.to_thread(
                self.translator.translate_text,
                text,
                target_lang='EN-US'
            )
            # Return normalized language code (e.g., 'en-us' -> 'en')
            detected = result.detected_source_lang.lower()
            return self._normalize_lang_code(detected)
        except Exception as e:
            logger.warning(f"DeepL Language detection error: {e}. Falling back to langdetect.")
            try:
                from langdetect import detect
                lang = await asyncio.to_thread(detect, text)
                return self._normalize_lang_code(lang)
            except Exception as e2:
                logger.error(f"Fallback detection error: {e2}")
                return 'en'
    
    def _normalize_lang_code(self, lang_code: str) -> str:
        """Normalize language code to base form (e.g., 'en-us' -> 'en', 'pt-br' -> 'pt')"""
        if not lang_code:
            return 'en'
        # Split on hyphen and take the first part
        base_lang = lang_code.split('-')[0].lower()
        return base_lang
    
    def get_supported_languages(self) -> dict:
        """Get supported languages (cached)"""
        if self._supported_languages:
            return self._supported_languages
        
        try:
            source_langs = self.translator.get_source_languages()
            target_langs = self.translator.get_target_languages()
            
            self._supported_languages = {
                'source': {lang.code.lower(): lang.name for lang in source_langs},
                'target': {lang.code.lower(): lang.name for lang in target_langs}
            }
            return self._supported_languages
        except Exception as e:
            logger.error(f"Failed to get supported languages: {e}")
            return {'source': {}, 'target': {}}


class AutoTranslate(commands.Cog):
    """Auto-translate messages between Discord channels using DeepL API"""
    
    def __init__(self, bot):
        self.bot = bot
        self.translator = None
        
        # Initialize DeepL translator
        api_key = os.getenv('DEEPL_API_KEY')
        if api_key:
            self.translator = DeepLTranslator(api_key)
            logger.info("DeepL translator initialized")
        else:
            logger.warning("DEEPL_API_KEY not found in environment variables")
        
        # Cache for webhooks
        self.webhooks = {}
    
    async def get_or_create_webhook(self, channel: discord.TextChannel, name: str) -> Optional[discord.Webhook]:
        """Get existing webhook or create new one"""
        try:
            # Check cache
            cache_key = f"{channel.id}:{name}"
            if cache_key in self.webhooks:
                webhook = self.webhooks[cache_key]
                # Verify webhook still has token
                if webhook.token:
                    return webhook
                else:
                    # Remove from cache if no token
                    del self.webhooks[cache_key]
            
            # Check existing webhooks
            webhooks = await channel.webhooks()
            for webhook in webhooks:
                if webhook.name == name and webhook.token:
                    self.webhooks[cache_key] = webhook
                    return webhook
            
            # Create new webhook
            webhook = await channel.create_webhook(name=name)
            self.webhooks[cache_key] = webhook
            return webhook
        except Exception as e:
            logger.error(f"Failed to get/create webhook: {e}")
            return None
    
    def _contains_non_ascii(self, text: str) -> bool:
        """Check if the text contains any non-ASCII characters (likely non-English script)"""
        return any(ord(c) > 127 for c in text)
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages in configured source channels"""
        # Ignore bot messages to prevent loops
        if message.author.bot:
            return
        
        # Ignore DMs
        if not message.guild:
            return
        
        # Check if translator is available
        if not self.translator or not mongo_enabled():
            return
        
        # Get configurations for this channel
        configs = AutoTranslateAdapter.get_configs_for_channel(message.channel.id)
        if not configs:
            return
        
        # Process each configuration
        for config in configs:
            try:
                await self._process_translation(message, config)
            except Exception as e:
                logger.error(f"Error processing translation for config {config.get('config_id')}: {e}")
    
    async def _process_translation(self, message: discord.Message, config: dict):
        """Process a single translation configuration"""
        try:
            # Skip if message is too short (language detection is unreliable for short text)
            min_text_length = config.get('min_text_length', 10)
            
            # Increase sensitivity for non-English scripts
            effective_min_length = min_text_length
            if self._contains_non_ascii(message.content):
                # For non-English scripts, we allow shorter strings (e.g., 2 characters)
                effective_min_length = 2
                
            if len(message.content.strip()) < effective_min_length:
                logger.info(f"Skipping translation: message too short ({len(message.content)} chars, effective minimum {effective_min_length})")
                return
            
            # Skip if message has attachments and skip_attachments is enabled
            if config.get('skip_attachments') and message.attachments:
                logger.info(f"Skipping translation: message has attachments and skip_attachments is enabled")
                return
            
            # Detect source language
            detected_lang = await self.translator.detect_language(message.content)
            logger.info(f"Detected language: {detected_lang} for text: {message.content[:50]}")
            
            # Apply ignore rules
            target_lang = config.get('target_language', '').lower()
            source_lang = config.get('source_language', '').lower() if config.get('source_language') else None
            
            # Normalize target language for comparison (e.g., 'en-us' -> 'en')
            target_lang_normalized = self.translator._normalize_lang_code(target_lang)
            
            logger.info(f"Translation config: source={source_lang}, target={target_lang_normalized}, detected={detected_lang}")
            
            # Ignore if source is target (with normalized comparison)
            if config.get('ignore_if_source_is_target') and detected_lang == target_lang_normalized:
                logger.info(f"Skipping translation: detected language ({detected_lang}) matches target ({target_lang_normalized})")
                return
            
            # Ignore if source is not input
            if config.get('ignore_if_source_is_not_input') and source_lang and detected_lang != source_lang:
                logger.info(f"Skipping translation: detected language ({detected_lang}) doesn't match specified source ({source_lang})")
                return
            
            # Translate message
            logger.info(f"Translating from {source_lang or 'auto'} to {target_lang}")
            translated_text = await self.translator.translate(
                message.content,
                target_lang,
                source_lang
            )
            logger.info(f"Translation result: {translated_text[:100]}")
            
            # Handle attachments
            attachment_text = ""
            if message.attachments and config.get('attachment_mode') == 'link':
                attachment_links = [att.url for att in message.attachments]
                attachment_text = "\n\n**Attachments:**\n" + "\n".join(attachment_links)
            
            # Get target channel
            target_channel = message.guild.get_channel(config.get('target_channel_id'))
            if not target_channel:
                logger.error(f"Target channel {config.get('target_channel_id')} not found")
                return
            
            # Post translated message
            if config.get('style') == 'webhook':
                # Use webhook to preserve author
                webhook_name = f"AutoTranslate: {config.get('name')}"
                webhook = await self.get_or_create_webhook(target_channel, webhook_name)
                
                if webhook:
                    try:
                        sent_message = await webhook.send(
                            content=translated_text + attachment_text,
                            username=message.author.display_name,
                            avatar_url=message.author.display_avatar.url,
                            wait=True
                        )
                    except Exception as e:
                        logger.error(f"Webhook send failed: {e}, falling back to bot posting")
                        # Fallback to bot posting
                        sent_message = await target_channel.send(
                            f"**{message.author.display_name}:** {translated_text}{attachment_text}"
                        )
                else:
                    # Fallback to bot posting
                    sent_message = await target_channel.send(
                        f"**{message.author.display_name}:** {translated_text}{attachment_text}"
                    )
            else:
                # Post as bot
                sent_message = await target_channel.send(
                    f"**{message.author.display_name}:** {translated_text}{attachment_text}"
                )
            
            # Delete original if configured
            if config.get('delete_original'):
                try:
                    await message.delete()
                except discord.Forbidden:
                    logger.warning(f"No permission to delete message in {message.channel.name}")
            
            # Auto-disappear if configured
            auto_disappear = config.get('auto_disappear')
            if auto_disappear and auto_disappear > 0:
                await asyncio.sleep(auto_disappear)
                try:
                    await sent_message.delete()
                except discord.NotFound:
                    pass  # Message already deleted
                except discord.Forbidden:
                    logger.warning(f"No permission to delete message in {target_channel.name}")
        
        except Exception as e:
            logger.error(f"Error in _process_translation: {e}")
    
    @app_commands.command(name="autotranslatecreate", description="Create automatic translation between channels")
    async def autotranslatecreate(self, interaction: discord.Interaction):
        """Create a new auto-translate configuration"""
        if not mongo_enabled():
            await interaction.response.send_message(
                "❌ Database not available. Auto-translate requires MongoDB.",
                ephemeral=True
            )
            return
        
        if not self.translator:
            await interaction.response.send_message(
                "❌ DeepL API key not configured. Please set DEEPL_API_KEY in environment variables.",
                ephemeral=True
            )
            return
        
        # Show initial modal for name and languages
        modal = ConfigNameModal(self)
        await interaction.response.send_modal(modal)
    
    @app_commands.command(name="autotranslatelist", description="View all auto-translate configurations")
    async def autotranslatelist(self, interaction: discord.Interaction):
        """List all configurations for this server"""
        if not mongo_enabled():
            await interaction.response.send_message(
                "❌ Database not available.",
                ephemeral=True
            )
            return
        
        configs = AutoTranslateAdapter.get_guild_configs(interaction.guild.id)
        
        if not configs:
            await interaction.response.send_message(
                "No auto-translate configurations found for this server.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="🌐 Auto-Translate Configurations",
            description=f"Found {len(configs)} configuration(s)",
            color=discord.Color.blue()
        )
        
        for config in configs:
            source_channel = interaction.guild.get_channel(config.get('source_channel_id'))
            target_channel = interaction.guild.get_channel(config.get('target_channel_id'))
            
            status = "✅ Enabled" if config.get('enabled') else "❌ Disabled"
            value = (
                f"**Source:** {source_channel.mention if source_channel else 'Unknown'}\n"
                f"**Target:** {target_channel.mention if target_channel else 'Unknown'}\n"
                f"**Language:** {config.get('source_language', 'auto')} → {config.get('target_language')}\n"
                f"**Style:** {config.get('style', 'bot')}\n"
                f"**Status:** {status}"
            )
            
            embed.add_field(
                name=f"📝 {config.get('name')}",
                value=value,
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="autotranslatedelete", description="Delete an auto-translate configuration")
    async def autotranslatedelete(self, interaction: discord.Interaction):
        """Delete a configuration"""
        if not mongo_enabled():
            await interaction.response.send_message(
                "❌ Database not available.",
                ephemeral=True
            )
            return
        
        configs = AutoTranslateAdapter.get_guild_configs(interaction.guild.id)
        
        if not configs:
            await interaction.response.send_message(
                "No configurations found to delete.",
                ephemeral=True
            )
            return
        
        view = DeleteConfigView(configs)
        await interaction.response.send_message(
            "Select a configuration to delete:",
            view=view,
            ephemeral=True
        )
    
    @app_commands.command(name="autotranslatetoggle", description="Enable/disable an auto-translate configuration")
    async def autotranslatetoggle(self, interaction: discord.Interaction):
        """Toggle a configuration"""
        if not mongo_enabled():
            await interaction.response.send_message(
                "❌ Database not available.",
                ephemeral=True
            )
            return
        
        configs = AutoTranslateAdapter.get_guild_configs(interaction.guild.id)
        
        if not configs:
            await interaction.response.send_message(
                "No configurations found.",
                ephemeral=True
            )
            return
        
        view = ToggleConfigView(configs)
        await interaction.response.send_message(
            "Select a configuration to toggle:",
            view=view,
            ephemeral=True
        )
    
    @app_commands.command(name="autotranslateedit", description="Edit an auto-translate configuration")
    async def autotranslateedit(self, interaction: discord.Interaction):
        """Edit a configuration"""
        if not mongo_enabled():
            await interaction.response.send_message(
                "❌ Database not available.",
                ephemeral=True
            )
            return
        
        if not self.translator:
            await interaction.response.send_message(
                "❌ DeepL API key not configured.",
                ephemeral=True
            )
            return
        
        configs = AutoTranslateAdapter.get_guild_configs(interaction.guild.id)
        
        if not configs:
            await interaction.response.send_message(
                "No configurations found to edit.",
                ephemeral=True
            )
            return
        
        view = EditConfigSelectionView(self, configs)
        await interaction.response.send_message(
            "Select a configuration to edit:",
            view=view,
            ephemeral=True
        )


class ConfigNameModal(discord.ui.Modal, title="Auto-Translate Configuration"):
    """Modal for collecting configuration name"""
    
    name_input = discord.ui.TextInput(
        label="Configuration Name",
        placeholder="e.g., English to Spanish",
        required=True,
        max_length=50
    )
    
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    
    async def on_submit(self, interaction: discord.Interaction):
        # Store config data
        config_data = {
            'name': self.name_input.value,
            'created_by': interaction.user.id
        }
        
        # Show language selection
        view = LanguageSelectionView(self.cog, config_data)
        embed = discord.Embed(
            title="🌐 Select Languages",
            description="Choose the target language and optionally the source language.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True
        )


class LanguageSelectionView(discord.ui.View):
    """View for selecting target and source languages"""
    
    def __init__(self, cog, config_data):
        super().__init__(timeout=300)
        self.cog = cog
        self.config_data = config_data
        
        # Add language selects
        self.add_item(TargetLanguageSelect())
        self.add_item(SourceLanguageSelect())
    
    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=2)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if target language is selected
        if 'target_language' not in self.config_data:
            await interaction.response.send_message(
                "❌ Please select a target language.",
                ephemeral=True
            )
            return
        
        # Show channel selection
        view = ChannelSelectionView(self.cog, self.config_data)
        embed = discord.Embed(
            title="📺 Select Channels",
            description="Choose the source and target channels for translation.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=view
        )


class TargetLanguageSelect(discord.ui.Select):
    """Select for target language"""
    
    def __init__(self):
        options = [
            discord.SelectOption(
                label=name,
                value=code,
                emoji=name.split()[0] if name.split()[0].startswith('�') else None
            )
            for code, name in sorted(SUPPORTED_LANGUAGES.items(), key=lambda x: x[1])
        ][:25]  # Discord limit
        
        super().__init__(
            placeholder="Select target language (required)",
            options=options,
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['target_language'] = self.values[0]
        await interaction.response.send_message(
            f"✅ Target language set to: {SUPPORTED_LANGUAGES[self.values[0]]}",
            ephemeral=True,
            delete_after=2
        )


class SourceLanguageSelect(discord.ui.Select):
    """Select for source language (optional)"""
    
    def __init__(self):
        options = [
            discord.SelectOption(
                label="🔄 Auto-Detect",
                value="auto",
                description="Automatically detect source language"
            )
        ]
        options.extend([
            discord.SelectOption(
                label=name,
                value=code,
                emoji=name.split()[0] if name.split()[0].startswith('�') else None
            )
            for code, name in sorted(SUPPORTED_LANGUAGES.items(), key=lambda x: x[1])
        ][:24])  # Discord limit (25 total with auto-detect)
        
        super().__init__(
            placeholder="Select source language (optional - defaults to auto-detect)",
            options=options,
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == 'auto':
            self.view.config_data['source_language'] = None
            await interaction.response.send_message(
                "✅ Source language set to: Auto-Detect",
                ephemeral=True,
                delete_after=2
            )
        else:
            self.view.config_data['source_language'] = self.values[0]
            await interaction.response.send_message(
                f"✅ Source language set to: {SUPPORTED_LANGUAGES[self.values[0]]}",
                ephemeral=True,
                delete_after=2
            )


class ChannelSelectionView(discord.ui.View):
    """View for selecting source and target channels"""
    
    def __init__(self, cog, config_data):
        super().__init__(timeout=300)
        self.cog = cog
        self.config_data = config_data
        
        # Add channel selects
        self.add_item(SourceChannelSelect())
        self.add_item(TargetChannelSelect())
    
    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=2)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if channels are selected
        if 'source_channel_id' not in self.config_data or 'target_channel_id' not in self.config_data:
            await interaction.response.send_message(
                "❌ Please select both source and target channels.",
                ephemeral=True
            )
            return
        
        # Show options view
        view = OptionsView(self.cog, self.config_data)
        embed = discord.Embed(
            title="⚙️ Configure Options",
            description="Customize the translation behavior.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=view
        )


class SourceChannelSelect(discord.ui.ChannelSelect):
    """Select for source channel"""
    
    def __init__(self):
        super().__init__(
            placeholder="Select source channel",
            channel_types=[discord.ChannelType.text],
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['source_channel_id'] = self.values[0].id
        await interaction.response.defer()


class TargetChannelSelect(discord.ui.ChannelSelect):
    """Select for target channel"""
    
    def __init__(self):
        super().__init__(
            placeholder="Select target channel",
            channel_types=[discord.ChannelType.text],
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['target_channel_id'] = self.values[0].id
        await interaction.response.defer()


class OptionsView(discord.ui.View):
    """View for configuring translation options"""
    
    def __init__(self, cog, config_data, is_edit=False):
        super().__init__(timeout=300)
        self.cog = cog
        self.config_data = config_data
        self.is_edit = is_edit
        
        # Set defaults
        self.config_data.setdefault('style', 'webhook')
        self.config_data.setdefault('delete_original', False)
        self.config_data.setdefault('auto_disappear', 0)
        self.config_data.setdefault('ignore_if_source_is_target', True)
        self.config_data.setdefault('ignore_if_source_is_not_input', False)
        self.config_data.setdefault('skip_attachments', False)
        self.config_data.setdefault('attachment_mode', 'link')
        self.config_data.setdefault('min_text_length', 10)  # Minimum message length to translate
        
        # Add option selects (one per row, max 5 rows)
        # Row 0: Style and Delete Original combined
        self.add_item(StyleAndDeleteSelect(
            self.config_data.get('style'),
            self.config_data.get('delete_original')
        ))
        # Row 1: Auto-Disappear
        self.add_item(AutoDisappearSelect(self.config_data.get('auto_disappear')))
        # Row 2: Ignore Same Language
        self.add_item(IgnoreSameLangSelect(self.config_data.get('ignore_if_source_is_target')))
        # Row 3: Source Filter and Skip Attachments combined
        self.add_item(SourceFilterAndAttachmentsSelect(
            self.config_data.get('ignore_if_source_is_not_input'),
            self.config_data.get('skip_attachments')
        ))
    
    @discord.ui.button(label="Save Configuration", style=discord.ButtonStyle.success, row=4)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.is_edit:
            # Update existing configuration
            config_id = self.config_data.pop('config_id')
            # Remove fields that shouldn't be updated
            updates = {k: v for k, v in self.config_data.items() if k not in ['guild_id', 'created_by', 'created_at', 'enabled']}
            
            if AutoTranslateAdapter.update_config(config_id, updates):
                await self._show_success_message(interaction, "✅ Auto-Translate Configuration Updated")
            else:
                await interaction.response.send_message(
                    "❌ Failed to update configuration. Please try again.",
                    ephemeral=True
                )
        else:
            # Create new configuration
            config_id = AutoTranslateAdapter.create_config(interaction.guild.id, self.config_data)
            
            if config_id:
                await self._show_success_message(interaction, "✅ Auto-Translate Configuration Created")
            else:
                await interaction.response.send_message(
                    "❌ Failed to create configuration. Please try again.",
                    ephemeral=True
                )
    
    async def _show_success_message(self, interaction: discord.Interaction, title: str):
        
        source_channel = interaction.guild.get_channel(self.config_data['source_channel_id'])
        target_channel = interaction.guild.get_channel(self.config_data['target_channel_id'])
        
        source_lang_display = SUPPORTED_LANGUAGES.get(
            self.config_data.get('source_language', ''), 
            '🔄 Auto-Detect'
        )
        target_lang_display = SUPPORTED_LANGUAGES.get(
            self.config_data['target_language'], 
            self.config_data['target_language'].upper()
        )
        
        embed = discord.Embed(
            title=title,
            description=f"**{self.config_data['name']}**",
            color=discord.Color.green()
        )
        embed.add_field(name="Source Channel", value=source_channel.mention, inline=True)
        embed.add_field(name="Target Channel", value=target_channel.mention, inline=True)
        embed.add_field(
            name="Language",
            value=f"{source_lang_display} → {target_lang_display}",
            inline=True
        )
        embed.add_field(name="Style", value=self.config_data['style'].title(), inline=True)
        embed.add_field(name="Delete Original", value="Yes" if self.config_data['delete_original'] else "No", inline=True)
        
        auto_disappear_display = "Never"
        if self.config_data['auto_disappear'] > 0:
            auto_disappear_display = f"{self.config_data['auto_disappear']}s"
        embed.add_field(name="Auto-Disappear", value=auto_disappear_display, inline=True)
        embed.add_field(name="Skip Attachments", value="Yes" if self.config_data.get('skip_attachments') else "No", inline=True)
        embed.add_field(name="Match Source Only", value="Yes" if self.config_data.get('ignore_if_source_is_not_input') else "No", inline=True)
        
        await interaction.response.edit_message(embed=embed, view=None)


class StyleAndDeleteSelect(discord.ui.Select):
    """Combined select for posting style and delete original"""
    
    def __init__(self, default_style='webhook', default_delete=False):
        default_value = f"{default_style}_{'delete' if default_delete else 'keep'}"
        options = [
            discord.SelectOption(
                label="Webhook (Keep Original)",
                value="webhook_keep",
                description="Post as user, keep original message",
                emoji="👤",
                default=(default_value == 'webhook_keep')
            ),
            discord.SelectOption(
                label="Webhook (Delete Original)",
                value="webhook_delete",
                description="Post as user, delete original",
                emoji="👤",
                default=(default_value == 'webhook_delete')
            ),
            discord.SelectOption(
                label="Bot (Keep Original)",
                value="bot_keep",
                description="Post as bot, keep original message",
                emoji="🤖",
                default=(default_value == 'bot_keep')
            ),
            discord.SelectOption(
                label="Bot (Delete Original)",
                value="bot_delete",
                description="Post as bot, delete original",
                emoji="🤖",
                default=(default_value == 'bot_delete')
            )
        ]
        
        super().__init__(
            placeholder="Select posting style and original message handling",
            options=options,
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        parts = self.values[0].split('_')
        self.view.config_data['style'] = parts[0]
        self.view.config_data['delete_original'] = (parts[1] == 'delete')
        await interaction.response.defer()


class AutoDisappearSelect(discord.ui.Select):
    """Select for auto-disappear timing"""
    
    def __init__(self, default_value=0):
        default_str = str(default_value)
        options = [
            discord.SelectOption(label="Never", value="0", description="Keep messages permanently", emoji="♾️", default=(default_str == "0")),
            discord.SelectOption(label="10 seconds", value="10", emoji="⏱️", default=(default_str == "10")),
            discord.SelectOption(label="30 seconds", value="30", emoji="⏱️", default=(default_str == "30")),
            discord.SelectOption(label="1 minute", value="60", emoji="⏱️", default=(default_str == "60")),
            discord.SelectOption(label="5 minutes", value="300", emoji="⏱️", default=(default_str == "300")),
            discord.SelectOption(label="10 minutes", value="600", emoji="⏱️", default=(default_str == "600")),
        ]
        
        super().__init__(
            placeholder="Auto-delete translated messages after...",
            options=options,
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['auto_disappear'] = int(self.values[0])
        await interaction.response.defer()


class IgnoreSameLangSelect(discord.ui.Select):
    """Select for ignoring same language"""
    
    def __init__(self, default_value=True):
        options = [
            discord.SelectOption(
                label="Yes",
                value="yes",
                description="Skip translation if detected language = target",
                emoji="✅",
                default=default_value
            ),
            discord.SelectOption(
                label="No",
                value="no",
                description="Always translate regardless of detected language",
                emoji="❌",
                default=(not default_value)
            )
        ]
        
        super().__init__(
            placeholder="Ignore if source language = target language?",
            options=options,
            row=2
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['ignore_if_source_is_target'] = self.values[0] == 'yes'
        await interaction.response.defer()


class SourceFilterAndAttachmentsSelect(discord.ui.Select):
    """Combined select for source filter and skip attachments"""
    
    def __init__(self, default_filter=False, default_skip=False):
        default_value = f"{'filter' if default_filter else 'all'}_{'skip' if default_skip else 'translate'}"
        options = [
            discord.SelectOption(
                label="Translate All (Including Attachments)",
                value="all_translate",
                description="Translate all messages, including those with attachments",
                emoji="🌐",
                default=(default_value == 'all_translate')
            ),
            discord.SelectOption(
                label="Translate All (Skip Attachments)",
                value="all_skip",
                description="Translate all, but skip messages with attachments",
                emoji="🚫",
                default=(default_value == 'all_skip')
            ),
            discord.SelectOption(
                label="Match Source Only (Including Attachments)",
                value="filter_translate",
                description="Only translate matching source, including attachments",
                emoji="🎯",
                default=(default_value == 'filter_translate')
            ),
            discord.SelectOption(
                label="Match Source Only (Skip Attachments)",
                value="filter_skip",
                description="Only translate matching source, skip attachments",
                emoji="🎯",
                default=(default_value == 'filter_skip')
            )
        ]
        
        super().__init__( placeholder="Translation filters (source matching & attachments)",
            options=options,
            row=3
        )
    
    async def callback(self, interaction: discord.Interaction):
        parts = self.values[0].split('_')
        self.view.config_data['ignore_if_source_is_not_input'] = (parts[0] == 'filter')
        self.view.config_data['skip_attachments'] = (parts[1] == 'skip')
        await interaction.response.defer()



class AttachmentModeSelect(discord.ui.Select):
    """Select for attachment handling"""
    
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Link",
                value="link",
                description="Include attachment links in translated message",
                emoji="🔗",
                default=True
            ),
            discord.SelectOption(
                label="Ignore",
                value="ignore",
                description="Don't include attachments in translation",
                emoji="🚫"
            )
        ]
        
        super().__init__(
            placeholder="How to handle attachments?",
            options=options,
            row=4
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['attachment_mode'] = self.values[0]
        await interaction.response.defer()


class DeleteConfigView(discord.ui.View):
    """View for deleting configurations"""
    
    def __init__(self, configs):
        super().__init__(timeout=300)
        self.add_item(DeleteConfigSelect(configs))


class DeleteConfigSelect(discord.ui.Select):
    """Select for choosing config to delete"""
    
    def __init__(self, configs):
        options = [
            discord.SelectOption(
                label=config.get('name'),
                value=config.get('config_id'),
                description=f"{config.get('source_language', 'auto')} → {config.get('target_language')}"
            )
            for config in configs[:25]  # Discord limit
        ]
        
        super().__init__(
            placeholder="Select configuration to delete",
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        config_id = self.values[0]
        
        if AutoTranslateAdapter.delete_config(config_id):
            await interaction.response.send_message(
                "✅ Configuration deleted successfully.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Failed to delete configuration.",
                ephemeral=True
            )


class ToggleConfigView(discord.ui.View):
    """View for toggling configurations"""
    
    def __init__(self, configs):
        super().__init__(timeout=300)
        self.add_item(ToggleConfigSelect(configs))


class ToggleConfigSelect(discord.ui.Select):
    """Select for choosing config to toggle"""
    
    def __init__(self, configs):
        options = [
            discord.SelectOption(
                label=config.get('name'),
                value=config.get('config_id'),
                description=f"{'✅ Enabled' if config.get('enabled') else '❌ Disabled'}",
                emoji="✅" if config.get('enabled') else "❌"
            )
            for config in configs[:25]  # Discord limit
        ]
        
        super().__init__(
            placeholder="Select configuration to toggle",
            options=options
        )
        self.configs = {c.get('config_id'): c for c in configs}
    
    async def callback(self, interaction: discord.Interaction):
        config_id = self.values[0]
        config = self.configs.get(config_id)
        
        if not config:
            await interaction.response.send_message(
                "❌ Configuration not found.",
                ephemeral=True
            )
            return
        
        new_state = not config.get('enabled')
        
        if AutoTranslateAdapter.toggle_config(config_id, new_state):
            status = "enabled" if new_state else "disabled"
            await interaction.response.send_message(
                f"✅ Configuration **{config.get('name')}** {status}.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Failed to toggle configuration.",
                ephemeral=True
            )




class EditConfigSelectionView(discord.ui.View):
    """View for selecting which config to edit"""
    
    def __init__(self, cog, configs):
        super().__init__(timeout=300)
        self.cog = cog
        self.add_item(EditConfigSelect(cog, configs))


class EditConfigSelect(discord.ui.Select):
    """Select for choosing config to edit"""
    
    def __init__(self, cog, configs):
        self.cog = cog
        options = [
            discord.SelectOption(
                label=config.get('name'),
                value=config.get('config_id'),
                description=f"{config.get('source_language', 'auto')} → {config.get('target_language')}"
            )
            for config in configs[:25]  # Discord limit
        ]
        
        super().__init__(
            placeholder="Select configuration to edit",
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        config_id = self.values[0]
        config = AutoTranslateAdapter.get_config(config_id)
        
        if not config:
            await interaction.response.send_message(
                "❌ Configuration not found.",
                ephemeral=True
            )
            return
        
        # Show edit view with current settings
        view = EditLanguageSelectionView(self.cog, config)
        embed = discord.Embed(
            title="🌐 Edit Languages",
            description=f"Editing: **{config.get('name')}**\n\nChoose the target language and optionally the source language.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=view
        )


class EditLanguageSelectionView(discord.ui.View):
    """View for editing target and source languages"""
    
    def __init__(self, cog, config_data):
        super().__init__(timeout=300)
        self.cog = cog
        self.config_data = config_data
        
        # Add language selects with current values
        current_target = config_data.get('target_language')
        current_source = config_data.get('source_language')
        
        self.add_item(EditTargetLanguageSelect(current_target))
        self.add_item(EditSourceLanguageSelect(current_source))
    
    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=2)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if target language is selected
        if 'target_language' not in self.config_data:
            await interaction.response.send_message(
                "❌ Please select a target language.",
                ephemeral=True
            )
            return
        
        # Show channel selection
        view = EditChannelSelectionView(self.cog, self.config_data)
        embed = discord.Embed(
            title="📺 Edit Channels",
            description="Choose the source and target channels for translation.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=view
        )


class EditTargetLanguageSelect(discord.ui.Select):
    """Select for editing target language"""
    
    def __init__(self, current_value=None):
        options = [
            discord.SelectOption(
                label=name,
                value=code,
                emoji=name.split()[0] if name.split()[0].startswith('🇦') or name.split()[0].startswith('🇧') or name.split()[0].startswith('🇨') or name.split()[0].startswith('🇩') or name.split()[0].startswith('🇪') or name.split()[0].startswith('🇫') or name.split()[0].startswith('🇬') or name.split()[0].startswith('🇭') or name.split()[0].startswith('🇮') or name.split()[0].startswith('🇯') or name.split()[0].startswith('🇰') or name.split()[0].startswith('🇱') or name.split()[0].startswith('🇲') or name.split()[0].startswith('🇳') or name.split()[0].startswith('🇴') or name.split()[0].startswith('🇵') or name.split()[0].startswith('🇷') or name.split()[0].startswith('🇸') or name.split()[0].startswith('🇹') or name.split()[0].startswith('🇺') or name.split()[0].startswith('🇿') else None,
                default=(code == current_value)
            )
            for code, name in sorted(SUPPORTED_LANGUAGES.items(), key=lambda x: x[1])
        ][:25]  # Discord limit
        
        super().__init__(
            placeholder="Select target language (required)",
            options=options,
            row=0
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['target_language'] = self.values[0]
        await interaction.response.send_message(
            f"✅ Target language set to: {SUPPORTED_LANGUAGES[self.values[0]]}",
            ephemeral=True,
            delete_after=2
        )


class EditSourceLanguageSelect(discord.ui.Select):
    """Select for editing source language (optional)"""
    
    def __init__(self, current_value=None):
        options = [
            discord.SelectOption(
                label="🔄 Auto-Detect",
                value="auto",
                description="Automatically detect source language",
                default=(current_value is None or current_value == 'auto')
            )
        ]
        options.extend([
            discord.SelectOption(
                label=name,
                value=code,
                emoji=name.split()[0] if name.split()[0].startswith('🇦') or name.split()[0].startswith('🇧') or name.split()[0].startswith('🇨') or name.split()[0].startswith('🇩') or name.split()[0].startswith('🇪') or name.split()[0].startswith('🇫') or name.split()[0].startswith('🇬') or name.split()[0].startswith('🇭') or name.split()[0].startswith('🇮') or name.split()[0].startswith('🇯') or name.split()[0].startswith('🇰') or name.split()[0].startswith('🇱') or name.split()[0].startswith('🇲') or name.split()[0].startswith('🇳') or name.split()[0].startswith('🇴') or name.split()[0].startswith('🇵') or name.split()[0].startswith('🇷') or name.split()[0].startswith('🇸') or name.split()[0].startswith('🇹') or name.split()[0].startswith('🇺') or name.split()[0].startswith('🇿') else None,
                default=(code == current_value)
            )
            for code, name in sorted(SUPPORTED_LANGUAGES.items(), key=lambda x: x[1])
        ][:24])  # Discord limit (25 total with auto-detect)
        
        super().__init__(
            placeholder="Select source language (optional - defaults to auto-detect)",
            options=options,
            row=1
        )
    
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == 'auto':
            self.view.config_data['source_language'] = None
            await interaction.response.send_message(
                "✅ Source language set to: Auto-Detect",
                ephemeral=True,
                delete_after=2
            )
        else:
            self.view.config_data['source_language'] = self.values[0]
            await interaction.response.send_message(
                f"✅ Source language set to: {SUPPORTED_LANGUAGES[self.values[0]]}",
                ephemeral=True,
                delete_after=2
            )


class EditChannelSelectionView(discord.ui.View):
    """View for editing source and target channels"""
    
    def __init__(self, cog, config_data):
        super().__init__(timeout=300)
        self.cog = cog
        self.config_data = config_data
        
        # Add channel selects
        self.add_item(EditSourceChannelSelect(config_data.get('source_channel_id')))
        self.add_item(EditTargetChannelSelect(config_data.get('target_channel_id')))
    
    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=2)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if channels are selected
        if 'source_channel_id' not in self.config_data or 'target_channel_id' not in self.config_data:
            await interaction.response.send_message(
                "❌ Please select both source and target channels.",
                ephemeral=True
            )
            return
        
        # Show options view
        view = OptionsView(self.cog, self.config_data, is_edit=True)
        embed = discord.Embed(
            title="⚙️ Configure Options",
            description="Customize the translation behavior.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(
            embed=embed,
            view=view
        )


class EditSourceChannelSelect(discord.ui.ChannelSelect):
    """Select for editing source channel"""
    
    def __init__(self, current_channel_id=None):
        super().__init__(
            placeholder="Select source channel",
            channel_types=[discord.ChannelType.text],
            row=0,
            default_values=[discord.Object(id=current_channel_id)] if current_channel_id else None
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['source_channel_id'] = self.values[0].id
        await interaction.response.defer()


class EditTargetChannelSelect(discord.ui.ChannelSelect):
    """Select for editing target channel"""
    
    def __init__(self, current_channel_id=None):
        super().__init__(
            placeholder="Select target channel",
            channel_types=[discord.ChannelType.text],
            row=1,
            default_values=[discord.Object(id=current_channel_id)] if current_channel_id else None
        )
    
    async def callback(self, interaction: discord.Interaction):
        self.view.config_data['target_channel_id'] = self.values[0].id
        await interaction.response.defer()


async def setup(bot):
    await bot.add_cog(AutoTranslate(bot))
