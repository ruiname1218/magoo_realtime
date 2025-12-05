#!/usr/bin/env python3
"""Simple test to verify Fish Audio TTS streaming works"""

import asyncio
from dotenv import load_dotenv
from fishaudio import AsyncFishAudio
from fishaudio.types import TTSConfig
import subprocess
import tempfile
import os

load_dotenv()

async def main():
    print("Initializing Fish Audio client...")
    client = AsyncFishAudio(api_key=os.getenv('FISH_API_KEY'))

    print("Creating text generator...")
    async def text_generator():
        text = "こんにちは、聞こえています。どうされましたか？"
        print(f"Yielding text: {text}")
        yield text

    print("Starting TTS streaming...")
    tts_config = TTSConfig(
        format="mp3",
        latency="balanced",
        chunk_length=150
    )

    audio_stream = client.tts.stream_websocket(
        text_generator(),
        config=tts_config
    )

    print("Collecting audio chunks...")
    chunks = []
    async for chunk in audio_stream:
        print(f"Got chunk: {len(chunk)} bytes")
        chunks.append(chunk)

    print(f"Total chunks: {len(chunks)}")

    if chunks:
        audio_bytes = b''.join(chunks)
        print(f"Total audio: {len(audio_bytes)} bytes")

        # Save and play
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        print(f"Playing {tmp_path}...")
        subprocess.run(['mpg123', tmp_path])
        os.unlink(tmp_path)
        print("Done!")
    else:
        print("No audio received!")

if __name__ == "__main__":
    asyncio.run(main())
