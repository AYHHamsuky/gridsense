"""Thin wrappers around the Meta Model API (OpenAI-compatible chat + ASR).

Docs: https://dev.meta.ai/docs/protocols/chat-completions
      https://dev.meta.ai/docs/api-reference/voice/transcribe
"""
import json

import httpx
from openai import OpenAI

from .config import META_BASE_URL, MODEL_API_KEY, TRANSCRIBE_MODEL

_client = None


def chat_client() -> OpenAI:
    global _client
    if _client is None:
        if not MODEL_API_KEY:
            raise RuntimeError("MODEL_API_KEY is not set. Copy .env.example to .env and add your key.")
        _client = OpenAI(base_url=META_BASE_URL, api_key=MODEL_API_KEY)
    return _client


def transcribe(audio_bytes: bytes, filename: str = "audio.wav") -> dict:
    """Transcribe a recorded voice note with Muse Voice Transcribe.

    Multipart body: `request` (JSON settings) + `audio` (the clip). WAV for now;
    convert other formats with ffmpeg before calling. Verify field values
    against the Voice schemas page on Day 1.
    """
    if not MODEL_API_KEY:
        raise RuntimeError("MODEL_API_KEY is not set.")
    settings = {"model": TRANSCRIBE_MODEL, "encoding": "WAV", "mode": "PUSH_TO_TALK"}
    r = httpx.post(
        f"{META_BASE_URL}/asr/transcribe",
        headers={"Authorization": f"Bearer {MODEL_API_KEY}", "Accept": "application/json"},
        files={
            "request": (None, json.dumps(settings), "application/json"),
            "audio": (filename, audio_bytes, "audio/wav"),
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()
