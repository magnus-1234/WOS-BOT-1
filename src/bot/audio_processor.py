"""
Audio Processing Utility for Voice Conversations
Handles audio format conversions, normalization, and TTS generation
"""

import os
import io
import asyncio
import tempfile
from typing import Optional, BinaryIO
from pathlib import Path

try:
    import speech_recognition as sr
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    from gtts import gTTS
    import whisper
except ImportError as e:
    print(f"⚠️ Audio processing dependencies not installed: {e}")
    print("Please run: pip install openai-whisper gtts SpeechRecognition pydub soundfile")


class AudioProcessor:
    """Handles all audio processing tasks for voice conversations"""
    
    def __init__(self, model_size: str = "base"):
        """
        Initialize audio processor with Whisper model
        
        Args:
            model_size: Whisper model size (tiny/base/small/medium/large)
                       - tiny: ~140MB, fastest, ~75% accuracy
                       - base: ~450MB, balanced, ~85% accuracy (recommended)
                       - small: ~950MB, better accuracy, ~90%
        """
        self.model_size = model_size
        self._whisper_model = None
        self.temp_dir = Path(tempfile.gettempdir()) / "discord_voice_chat"
        self.temp_dir.mkdir(exist_ok=True)
        
        print(f"🎙️ AudioProcessor initialized (Whisper model: {model_size} - will load on first use)")
    
    @property
    def whisper_model(self):
        """Lazy loader for Whisper model"""
        if self._whisper_model is None:
            print(f"📥 Loading Whisper {self.model_size} model (Sync fallback if async not used)...")
            self._whisper_model = whisper.load_model(self.model_size)
        return self._whisper_model

    @whisper_model.setter
    def whisper_model(self, value):
        self._whisper_model = value
    
    async def load_whisper_model(self):
        """Load Whisper model asynchronously (downloads on first use)"""
        if self._whisper_model is None:
            print(f"📥 Loading Whisper {self.model_size} model (this may take a moment)...")
            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            self._whisper_model = await loop.run_in_executor(
                None, lambda: whisper.load_model(self.model_size)
            )
            print(f"✅ Whisper {self.model_size} model loaded successfully")
        return self.whisper_model
    
    async def speech_to_text(self, audio_data: bytes, format: str = "wav") -> Optional[str]:
        """
        Convert speech audio to text using Whisper
        
        Args:
            audio_data: Raw audio data bytes
            format: Audio format (wav, mp3, etc.)
        
        Returns:
            Transcribed text or None if failed
        """
        try:
            # Ensure model is loaded
            model = await self.load_whisper_model()
            
            # Save audio data to temporary file
            temp_audio_path = self.temp_dir / f"temp_input_{os.getpid()}.{format}"
            with open(temp_audio_path, "wb") as f:
                f.write(audio_data)
            
            # Convert to WAV if needed
            if format != "wav":
                audio = AudioSegment.from_file(temp_audio_path, format=format)
                temp_wav_path = self.temp_dir / f"temp_input_{os.getpid()}.wav"
                audio.export(temp_wav_path, format="wav")
                temp_audio_path = temp_wav_path
            
            # Transcribe using Whisper
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                model.transcribe,
                str(temp_audio_path)
            )
            
            text = result.get("text", "").strip()
            
            # Cleanup temp file
            try:
                temp_audio_path.unlink()
            except:
                pass
            
            return text if text else None
            
        except Exception as e:
            print(f"❌ Error in speech-to-text: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def text_to_speech(self, text: str, language: str = "en", slow: bool = False) -> Optional[bytes]:
        """
        Convert text to speech audio using gTTS
        
        Args:
            text: Text to convert to speech
            language: Language code (en, es, fr, etc.)
            slow: Slower speech rate for clarity
        
        Returns:
            Audio data bytes (MP3 format) or None if failed
        """
        try:
            # Generate TTS
            loop = asyncio.get_event_loop()
            tts = await loop.run_in_executor(
                None,
                lambda: gTTS(text=text, lang=language, slow=slow)
            )
            
            # Save to bytes
            audio_bytes = io.BytesIO()
            await loop.run_in_executor(
                None,
                tts.write_to_fp,
                audio_bytes
            )
            audio_bytes.seek(0)
            
            return audio_bytes.read()
            
        except Exception as e:
            print(f"❌ Error in text-to-speech: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def convert_to_pcm(self, audio_data: bytes, source_format: str = "mp3") -> Optional[bytes]:
        """
        Convert audio to PCM format for Discord playback
        
        Args:
            audio_data: Input audio bytes
            source_format: Source audio format
        
        Returns:
            PCM audio bytes or None if failed
        """
        try:
            # Load audio
            audio = AudioSegment.from_file(io.BytesIO(audio_data), format=source_format)
            
            # Convert to Discord-compatible format
            # 48kHz, 2 channels (stereo), 16-bit
            audio = audio.set_frame_rate(48000).set_channels(2).set_sample_width(2)
            
            # Export as raw PCM
            pcm_bytes = io.BytesIO()
            audio.export(pcm_bytes, format="s16le", codec="pcm_s16le")
            pcm_bytes.seek(0)
            
            return pcm_bytes.read()
            
        except Exception as e:
            print(f"❌ Error converting to PCM: {e}")
            return None
    
    def normalize_audio(self, audio_data: bytes, format: str = "wav") -> bytes:
        """
        Normalize audio levels for better recognition
        
        Args:
            audio_data: Input audio bytes
            format: Audio format
        
        Returns:
            Normalized audio bytes
        """
        try:
            audio = AudioSegment.from_file(io.BytesIO(audio_data), format=format)
            
            # Normalize to -20 dBFS (good level for speech)
            change_in_dBFS = -20.0 - audio.dBFS
            normalized = audio.apply_gain(change_in_dBFS)
            
            # Export normalized audio
            output = io.BytesIO()
            normalized.export(output, format=format)
            output.seek(0)
            
            return output.read()
            
        except Exception as e:
            print(f"⚠️ Error normalizing audio (using original): {e}")
            return audio_data
    
    def split_by_silence(self, audio_data: bytes, format: str = "wav", 
                        min_silence_len: int = 500, silence_thresh: int = -40) -> list:
        """
        Split audio into chunks based on silence
        Useful for detecting when user stops speaking
        
        Args:
            audio_data: Input audio bytes
            format: Audio format
            min_silence_len: Minimum silence length in ms to split on
            silence_thresh: Silence threshold in dBFS
        
        Returns:
            List of audio chunks (AudioSegment objects)
        """
        try:
            audio = AudioSegment.from_file(io.BytesIO(audio_data), format=format)
            chunks = split_on_silence(
                audio,
                min_silence_len=min_silence_len,
                silence_thresh=silence_thresh,
                keep_silence=200  # Keep 200ms of silence for natural sound
            )
            return chunks
            
        except Exception as e:
            print(f"⚠️ Error splitting audio: {e}")
            return []
    
    async def save_temp_audio(self, audio_data: bytes, format: str = "mp3") -> Optional[str]:
        """
        Save audio data to a temporary file
        
        Args:
            audio_data: Audio bytes to save
            format: File format
        
        Returns:
            Path to temp file or None if failed
        """
        try:
            temp_path = self.temp_dir / f"voice_response_{os.getpid()}.{format}"
            with open(temp_path, "wb") as f:
                f.write(audio_data)
            return str(temp_path)
        except Exception as e:
            print(f"❌ Error saving temp audio: {e}")
            return None
    
    def cleanup_temp_files(self):
        """Clean up all temporary audio files"""
        try:
            for file in self.temp_dir.glob("*"):
                try:
                    file.unlink()
                except:
                    pass
        except Exception as e:
            print(f"⚠️ Error cleaning up temp files: {e}")


# Global instance
audio_processor = AudioProcessor(model_size="base")
