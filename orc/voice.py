import os
import tempfile
import numpy as np
from orc.logger import get_logger

logger = get_logger('voice')


class VoiceManager:
    """Handles STT (Speech-to-Text) and TTS (Text-to-Speech) with Paradee & Whisper."""

    def __init__(self, config: dict):
        voice_config = config.get('voice', {})
        self.stt_enabled = voice_config.get('stt_enabled', True)
        self.tts_enabled = voice_config.get('tts_enabled', True)
        self.tts_engine = voice_config.get('tts_engine', 'paradee')
        self.paradee_voice = voice_config.get('paradee_voice', 'af_heart')
        self.whisper_model = voice_config.get('whisper_model', 'tiny')
        self.tts_rate = voice_config.get('tts_rate', 175)
        self.tts_voice = voice_config.get('tts_voice', 0)
        self.listen_duration = voice_config.get('listen_duration', 5)

        self._stt_model = None
        self._paradee_model = None

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

    def _init_paradee(self):
        """Initialize Paradee 1.2 neural TTS model."""
        if self._paradee_model is not None:
            return True

        if not self.tts_enabled:
            return False

        try:
            from paradee import Paradee
            logger.info("Loading Paradee 1.2 TTS model...")
            self._paradee_model = Paradee()
            logger.info("Paradee 1.2 model loaded")
            return True
        except ImportError:
            logger.warning("paradee not installed. Run: pip install git+https://github.com/sahilmahendrakar/paradee")
            return False
        except Exception as e:
            logger.error(f"Failed to load Paradee: {e}")
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
            # Record normalized float32 audio [-1.0, 1.0]
            audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype='float32')
            sd.wait()

            audio_flat = audio.flatten()

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

    def _speak_paradee(self, text: str) -> bool:
        """Speak using Paradee 1.2 neural TTS."""
        if not self._init_paradee():
            return False
        try:
            import sounddevice as sd
            from paradee import SAMPLE_RATE
            audio = self._paradee_model(text, voice=self.paradee_voice)
            sd.play(audio, SAMPLE_RATE)
            sd.wait()
            return True
        except Exception as e:
            logger.error(f"Paradee TTS failed: {e}")
            return False

    def _speak_pyttsx3(self, text: str) -> bool:
        """Fallback TTS using Windows SAPI5 (pyttsx3)."""
        try:
            import pyttsx3
            # Initialize engine in current thread context to avoid Windows COM apartment deadlocks
            engine = pyttsx3.init()
            engine.setProperty('rate', self.tts_rate)

            voices = engine.getProperty('voices')
            if self.tts_voice < len(voices):
                engine.setProperty('voice', voices[self.tts_voice].id)

            engine.say(text)
            engine.runAndWait()
            return True
        except ImportError:
            logger.warning("pyttsx3 not installed. Run: pip install pyttsx3")
            return False
        except Exception as e:
            logger.error(f"pyttsx3 TTS failed: {e}")
            return False

    def speak(self, text: str) -> bool:
        """Convert text to speech using configured engine (paradee or pyttsx3 fallback)."""
        if not self.tts_enabled or not text:
            return False

        logger.info(f"Speaking: {text[:50]}...")
        if self.tts_engine == 'paradee':
            if self._speak_paradee(text):
                return True
            logger.warning("Paradee speak failed, attempting pyttsx3 fallback...")

        return self._speak_pyttsx3(text)

    def get_voices(self):
        """List available voices for both Paradee and pyttsx3."""
        voices = {
            'paradee': ['af_heart', 'am_puck', 'bf_isabella', 'bm_fable'],
            'pyttsx3': []
        }
        try:
            import pyttsx3
            engine = pyttsx3.init()
            sapi_voices = engine.getProperty('voices')
            voices['pyttsx3'] = [(i, v.name, v.id) for i, v in enumerate(sapi_voices)]
        except Exception:
            pass
        return voices
