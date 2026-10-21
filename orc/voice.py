import os
import tempfile
import numpy as np
from orc.logger import get_logger

logger = get_logger('voice')


class VoiceManager:
    """Handles STT (Speech-to-Text) and TTS (Text-to-Speech)."""

    def __init__(self, config: dict):
        voice_config = config.get('voice', {})
        self.stt_enabled = voice_config.get('stt_enabled', True)
        self.tts_enabled = voice_config.get('tts_enabled', True)
        self.whisper_model = voice_config.get('whisper_model', 'tiny')
        self.tts_rate = voice_config.get('tts_rate', 175)
        self.tts_voice = voice_config.get('tts_voice', 0)
        self.listen_duration = voice_config.get('listen_duration', 5)

        self._stt_model = None
        self._tts_engine = None

    def _init_stt(self):
        """Initialize Whisper model for STT."""
        if self._stt_model is not None:
            return True

        if not self.stt_enabled:
            return False

        try:
            import whisper
            logger.info(f"Loading Whisper model: {self.whisper_model}")
            self._stt_model = whisper.load_model(self.whisper_model)
            logger.info("Whisper model loaded")
            return True
        except ImportError:
            logger.warning("whisper not installed. Run: pip install openai-whisper")
            return False
        except Exception as e:
            logger.error(f"Failed to load Whisper: {e}")
            return False

    def _init_tts(self):
        """Initialize pyttsx3 for TTS."""
        if self._tts_engine is not None:
            return True

        if not self.tts_enabled:
            return False

        try:
            import pyttsx3
            self._tts_engine = pyttsx3.init()
            self._tts_engine.setProperty('rate', self.tts_rate)

            voices = self._tts_engine.getProperty('voices')
            if self.tts_voice < len(voices):
                self._tts_engine.setProperty('voice', voices[self.tts_voice].id)

            logger.info("TTS engine initialized")
            return True
        except ImportError:
            logger.warning("pyttsx3 not installed. Run: pip install pyttsx3")
            return False
        except Exception as e:
            logger.error(f"Failed to init TTS: {e}")
            return False

    def listen(self, duration=None):
        """Record audio from microphone and transcribe with Whisper."""
        if not self._init_stt():
            return None

        duration = duration or self.listen_duration

        try:
            import sounddevice as sd
            import whisper

            logger.info(f"Listening for {duration} seconds...")
            sample_rate = 16000
            audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1)
            sd.wait()

            audio_flat = audio.flatten().astype(np.float32) / 32768.0

            result = self._stt_model.transcribe(audio_flat, fp16=False)
            text = result.get('text', '').strip()

            if text:
                logger.info(f"Transcribed: {text}")
            else:
                logger.info("No speech detected")

            return text

        except ImportError:
            logger.warning("sounddevice not installed. Run: pip install sounddevice")
            return None
        except Exception as e:
            logger.error(f"Listen failed: {e}")
            return None

    def speak(self, text):
        """Convert text to speech."""
        if not self._init_tts():
            return False

        try:
            logger.info(f"Speaking: {text[:50]}...")
            self._tts_engine.say(text)
            self._tts_engine.runAndWait()
            return True
        except Exception as e:
            logger.error(f"TTS failed: {e}")
            return False

    def get_voices(self):
        """List available TTS voices."""
        if not self._init_tts():
            return []

        try:
            voices = self._tts_engine.getProperty('voices')
            return [(i, v.name, v.id) for i, v in enumerate(voices)]
        except Exception:
            return []
