#!/usr/bin/env python3
"""Standalone Paradee 1.2 Text-to-Speech CLI for Oryn.

Usage:
    python tools/speak.py "Hello world"
    python tools/speak.py "Good day, sir" --voice bm_fable
    python tools/speak.py "Saving this audio" -o speech.wav --no-play
    python tools/speak.py --list-voices
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path
import numpy as np


def save_wav(path: str | Path, audio: np.ndarray, sample_rate: int = 24000) -> None:
    """Save float32 audio as a standard 16-bit PCM mono WAV file."""
    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Paradee 1.2 standalone offline neural TTS engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Text to speak. If omitted, text is read from stdin.",
    )
    parser.add_argument(
        "-v",
        "--voice",
        default="af_heart",
        choices=["af_heart", "am_puck", "bf_isabella", "bm_fable"],
        help="Voice identifier (default: af_heart).",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Save generated speech to a WAV file path.",
    )
    parser.add_argument(
        "--no-play",
        action="store_true",
        help="Do not play audio through speakers (useful when exporting to file).",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List available Paradee 1.2 voices and exit.",
    )

    args = parser.parse_args()

    if args.list_voices:
        print("\nAvailable Paradee 1.2 Voices:")
        print("  • af_heart    - American Female (default, warm & expressive)")
        print("  • am_puck     - American Male   (natural, conversational)")
        print("  • bf_isabella - British Female  (clear, polite)")
        print("  • bm_fable    - British Male    (deep, articulated)\n")
        return 0

    text = " ".join(args.text).strip()
    if not text:
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()

    if not text:
        parser.print_help()
        return 1

    try:
        from paradee import Paradee, SAMPLE_RATE
    except ImportError:
        sys.stderr.write(
            "Error: Paradee is not installed. Run: pip install git+https://github.com/sahilmahendrakar/paradee --ignore-requires-python\n"
        )
        return 1

    print(f"\033[34m[paradee]\033[0m synthesizing with voice '\033[32m{args.voice}\033[0m'...")
    tts = Paradee()
    audio = tts(text, voice=args.voice)

    if args.output:
        save_wav(args.output, audio, SAMPLE_RATE)
        print(f"\033[34m[paradee]\033[0m saved audio to {args.output}")

    if not args.no_play:
        try:
            import sounddevice as sd
            print(f"\033[34m[paradee]\033[0m playing ({len(audio)/SAMPLE_RATE:.1f}s)...")
            sd.play(audio, SAMPLE_RATE)
            sd.wait()
        except Exception as e:
            sys.stderr.write(f"Playback error: {e}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
