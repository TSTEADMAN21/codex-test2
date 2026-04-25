#!/usr/bin/env python3
"""Standalone Kokoro TTS generator — runs under the kokoro-venv Python 3.14 interpreter.

Usage:
    python kokoro_generate.py --text "..." --voice bm_fable --output /path/to/out.mp3 [--speed 0.9]
"""
import argparse
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text",   required=True)
    parser.add_argument("--voice",  default="bm_fable")
    parser.add_argument("--output", required=True)
    parser.add_argument("--speed",  type=float, default=0.95)
    args = parser.parse_args()

    models_dir = Path(__file__).parent.parent / "models"
    onnx_path  = models_dir / "kokoro-v1.0.onnx"
    voices_path = models_dir / "voices-v1.0.bin"

    try:
        from kokoro_onnx import Kokoro
        import soundfile as sf
    except ImportError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    k = Kokoro(str(onnx_path), str(voices_path))
    samples, sr = k.create(args.text, voice=args.voice, speed=args.speed, lang="en-us")
    sf.write(args.output, samples, sr)

if __name__ == "__main__":
    main()
