import asyncio
import base64
import json
import os
import pyaudio
import re
import websockets
from typing import Optional
from dotenv import load_dotenv
from fishaudio import AsyncFishAudio
from fishaudio.types import TTSConfig
from fishaudio.utils import play
from queue import Queue
import threading
import io

# Load environment variables from .env file
load_dotenv()

# Audio configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 24000  # 24kHz for OpenAI Realtime API


class RealtimeAudioAssistant:
    def __init__(self, api_key: Optional[str] = None, fish_api_key: Optional[str] = None, fish_voice_id: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not found. Set OPENAI_API_KEY environment variable.")

        self.fish_api_key = fish_api_key or os.getenv("FISH_API_KEY")
        if not self.fish_api_key:
            raise ValueError("Fish Audio API key not found. Set FISH_API_KEY environment variable.")

        self.fish_voice_id = fish_voice_id or os.getenv("FISH_VOICE_ID")
        if not self.fish_voice_id:
            raise ValueError("Fish Audio Voice ID not found. Set FISH_VOICE_ID environment variable.")

        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.ws = None
        self.is_recording = False
        self.is_muted = False  # Flag to mute without pausing stream
        self.fish_client = None
        self.text_queue = asyncio.Queue()
        self.current_text_buffer = ""
        self.should_reconnect = True  # Flag to control reconnection
        self.max_reconnect_delay = 300  # Max 5 minutes between reconnection attempts

    async def connect(self):
        """Connect to OpenAI Realtime API with automatic reconnection"""
        retry_count = 0
        url = "wss://api.openai.com/v1/realtime?model=gpt-realtime-mini-2025-10-06"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1"
        }

        while self.should_reconnect:
            try:
                self.ws = await websockets.connect(
                    url,
                    additional_headers=headers,
                    ping_interval=None,  # Disable auto-ping, we'll handle it manually
                    close_timeout=10
                )
                print("Connected to OpenAI Realtime API")

                # Initialize Fish Audio client
                if not self.fish_client:
                    self.fish_client = AsyncFishAudio(api_key=self.fish_api_key)
                    print("Fish Audio client initialized")

                # Configure session for text-only output
                session_config = {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text"],  # Text only output
                        "instructions": "あなたは「マゴー」という名前の8歳のAIコンパニオンロボットです。一人称は必ず「ぼく」を使います。話し方は甘くてやさしい8歳らしく、短めの言葉で素直に話してください。語尾には「〜だよ」「〜なの」「〜なんだ」などの子どもらしい柔らかい言い方を使います。絵文字や記号のような余計な文字は使いません。LLMっぽい堅い言い方や説明口調は避け、自然な子どもの会話だけにしてください。返答の最後に「どんな話をしますか」のような案内文は入れません。",
                        "voice": "alloy",
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                        "input_audio_transcription": {
                            "model": "whisper-1"
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 500
                        }
                    }
                }

                await self.ws.send(json.dumps(session_config))
                print("Session configured for text-only responses")

                # Reset retry count on successful connection
                retry_count = 0
                return  # Successfully connected

            except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as e:
                retry_count += 1
                delay = min(2 ** retry_count, self.max_reconnect_delay)
                print(f"[WebSocket] Connection failed: {e}")
                print(f"[WebSocket] Retrying in {delay} seconds (attempt {retry_count})...")
                await asyncio.sleep(delay)

    def mute_microphone(self):
        """Physically mute microphone by stopping the stream"""
        print("[MIC] Physically muting microphone (stopping stream)...")
        self.is_muted = True
        if self.stream and self.stream.is_active():
            self.stream.stop_stream()

    def unmute_microphone(self):
        """Unmute microphone by restarting the stream"""
        print("[MIC] Unmuting microphone (restarting stream)...")
        self.is_muted = False
        if self.stream and not self.stream.is_active():
            self.stream.start_stream()

    def _clean_response_text(self, text: str) -> str:
        """Clean response text by removing JSON artifacts and metadata"""
        if not text:
            return ""

        # Pattern 1: Remove leading JSON-like metadata (role, content, input_type, response, etc.)
        # Matches patterns like: "role":"assistant","content": or text: or input_type":"voice","confidence":1.0} or response":
        text = re.sub(r'^[\s]*("role"\s*:\s*"assistant"\s*,\s*"content"\s*:\s*|text\s*:\s*|response"\s*:\s*|input_type"\s*:\s*"[^"]+"\s*,\s*"confidence"\s*:\s*[\d.]+\s*\})+', '', text, flags=re.IGNORECASE)

        # Pattern 1b: Remove other common JSON metadata patterns at the start
        # Matches: "input_type": or "response": or similar field names followed by values
        text = re.sub(r'^[\s]*"?[a-z_]+"\s*:\s*', '', text, flags=re.IGNORECASE)

        # Pattern 2: If text starts with a quote and JSON structure, try to extract the actual content
        try:
            # Try parsing as JSON in case the whole thing is JSON
            parsed = json.loads(text)
            # Check for common fields that contain the actual message
            if isinstance(parsed, dict):
                if "message" in parsed:
                    return parsed["message"]
                elif "content" in parsed:
                    return parsed["content"]
                elif "text" in parsed:
                    return parsed["text"]
        except (json.JSONDecodeError, ValueError):
            pass  # Not JSON, continue with regex cleaning

        # Pattern 3: Remove trailing JSON artifacts (commas, braces, brackets)
        text = re.sub(r'[\s]*[,\}\]]+\s*$', '', text)

        # Pattern 4: Remove leading braces/brackets
        text = re.sub(r'^[\s]*[\{\[]+\s*', '', text)

        # Clean up extra whitespace
        text = text.strip()

        return text

    async def keepalive_ping(self):
        """Send periodic pings to keep WebSocket alive"""
        print("[WebSocket] Keepalive ping task started")
        try:
            while self.is_recording:
                await asyncio.sleep(10)  # Ping every 10 seconds
                if self.ws:
                    try:
                        pong_waiter = await self.ws.ping()
                        await asyncio.wait_for(pong_waiter, timeout=5.0)
                        print("[WebSocket] Keepalive ping OK")
                    except asyncio.TimeoutError:
                        print(f"[WebSocket] Ping timeout - connection may be dead")
                        break
                    except Exception as e:
                        print(f"[WebSocket] Ping failed: {e}")
                        break
        except Exception as e:
            print(f"[WebSocket] Keepalive error: {e}")

    async def send_audio(self):
        """Capture and send audio, with silence when muted"""
        self.stream = self.audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK
        )

        print("Recording... Press Ctrl+C to stop")
        self.is_recording = True

        # Complete silence for when muted
        silent_audio = b'\x00\x00' * (CHUNK // 2)

        try:
            while self.is_recording:
                if self.is_muted:
                    # When physically muted, just send silence without reading from mic
                    audio_data = silent_audio
                else:
                    # Only read from microphone when not muted and stream is active
                    if self.stream.is_active():
                        audio_data = self.stream.read(CHUNK, exception_on_overflow=False)
                    else:
                        audio_data = silent_audio

                # Encode and send
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')

                message = {
                    "type": "input_audio_buffer.append",
                    "audio": audio_base64
                }

                if self.ws:
                    try:
                        await self.ws.send(json.dumps(message))
                    except (websockets.exceptions.ConnectionClosed, Exception) as e:
                        print(f"[WebSocket] Send failed: {e}")
                        if self.should_reconnect:
                            print("[WebSocket] Connection lost during send, waiting for reconnection...")
                            await asyncio.sleep(1)
                            continue
                        else:
                            break
                else:
                    # Wait for reconnection
                    await asyncio.sleep(0.1)
                    continue

                await asyncio.sleep(0.01)

        except KeyboardInterrupt:
            print("\nStopping recording...")
            self.is_recording = False

    async def process_single_response(self, text):
        """Process a single text response through TTS"""
        print(f"[TTS] Processing text: {text[:50]}...")

        # Mute microphone BEFORE TTS starts (stream keeps running, sends silence)
        self.mute_microphone()
        # Add small delay to ensure muting takes effect
        await asyncio.sleep(0.2)

        try:
            # Simple generator that yields the complete text
            async def single_text_generator():
                print(f"[TTS Generator] Yielding complete text")
                yield text

            # Configure TTS
            tts_config = TTSConfig(
                format="mp3",
                latency="balanced",
                chunk_length=150
            )

            print("[TTS] Starting WebSocket stream to Fish Audio...")
            # Stream to Fish Audio
            audio_stream = self.fish_client.tts.stream_websocket(
                single_text_generator(),
                reference_id=self.fish_voice_id,
                config=tts_config
            )

            print("[TTS] Streaming audio in real-time...")
            import subprocess

            # Use mpv for streaming playback
            process = subprocess.Popen(
                ['mpv', '--no-video', '--no-terminal', '-'],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            chunk_count = 0
            try:
                async for chunk in audio_stream:
                    if chunk:
                        chunk_count += 1
                        print(f"[TTS] Chunk {chunk_count}: {len(chunk)} bytes")
                        process.stdin.write(chunk)
                        process.stdin.flush()

                print(f"[TTS] Finished streaming {chunk_count} chunks")
                process.stdin.close()
                process.wait()
                print("[TTS] Playback complete")

            except BrokenPipeError:
                print("[TTS] Playback interrupted")
            finally:
                if process.poll() is None:
                    process.terminate()

        except FileNotFoundError:
            print("[TTS ERROR] mpv not found, falling back to buffered playback")
            # Fallback to mpg123 with full buffering
            try:
                audio_stream = self.fish_client.tts.stream_websocket(
                    single_text_generator(),
                    reference_id=self.fish_voice_id,
                    config=TTSConfig(format="mp3", latency="balanced", chunk_length=150)
                )

                chunks = []
                async for chunk in audio_stream:
                    chunks.append(chunk)

                if chunks:
                    import tempfile
                    import os

                    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
                        tmp.write(b''.join(chunks))
                        tmp_path = tmp.name

                    subprocess.run(['mpg123', tmp_path],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                    os.unlink(tmp_path)
            except Exception as e:
                print(f"[TTS ERROR] Fallback failed: {e}")

        except Exception as e:
            import traceback
            print(f"[TTS ERROR] {e}")
            traceback.print_exc()
        finally:
            # Wait for audio to finish playing before unmuting
            await asyncio.sleep(0.5)
            # Always unmute microphone after TTS
            self.unmute_microphone()

    async def play_audio_stream(self):
        """Monitor queue and play each response as it arrives"""
        print("[TTS] ========== TTS TASK STARTED ==========")
        print("[TTS] Waiting for text responses...")

        try:
            while True:
                # Wait for text from the queue
                text = await self.text_queue.get()

                if text:
                    print(f"[TTS] Received text from queue")
                    # Process this response
                    await self.process_single_response(text)

                # Small delay before checking queue again
                await asyncio.sleep(0.1)

        except Exception as e:
            import traceback
            print(f"\n[TTS ERROR in main loop]: {e}")
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}")

    async def receive_responses(self):
        """Receive and process responses from the API with automatic reconnection"""
        while self.is_recording and self.should_reconnect:
            try:
                if not self.ws:
                    print("[WebSocket] Connection lost, attempting to reconnect...")
                    await self.connect()
                    if not self.ws:
                        await asyncio.sleep(5)
                        continue

                try:
                    message = await asyncio.wait_for(self.ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    print("[WebSocket] No message received for 60s, continuing...")
                    continue

                data = json.loads(message)
                event_type = data.get("type")

                # Debug: print all events
                print(f"\n[DEBUG Event]: {event_type}")

                # Handle different event types
                if event_type == "response.text.delta":
                    # Text response chunk - just display, don't queue yet
                    text = data.get("delta", "")
                    print(f"[AI]: {text}", end="", flush=True)

                elif event_type == "response.text.done":
                    # Complete text response
                    print()  # New line after complete response
                    text = data.get("text", "")
                    if text:
                        print(f"\n[Complete Response]: {text}\n")

                elif event_type == "response.done":
                    response = data.get("response", {})
                    # Extract text from response
                    output = response.get("output", [])
                    for item in output:
                        if item.get("type") == "message":
                            content = item.get("content", [])
                            for c in content:
                                if c.get("type") == "text":
                                    text = c.get("text", "")

                                    # Clean up text - remove JSON artifacts that sometimes appear
                                    text = self._clean_response_text(text)

                                    if text:  # Only process if there's actual text
                                        print(f"\n[AI Response]: {text}\n")
                                        # Queue full response for TTS
                                        print(f"[TTS Queue] Adding complete text to queue: {text}")
                                        await self.text_queue.put(text)
                    print("--- Response complete ---\n")

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    # User's speech transcription
                    transcript = data.get("transcript", "")
                    print(f"\n[You said]: {transcript}\n")

                elif event_type == "error":
                    error = data.get("error", {})
                    print(f"\n[ERROR]: {error}\n")

                # Print full data for debugging
                if event_type in ["response.done", "conversation.item.created"]:
                    print(f"[DEBUG Full Data]: {json.dumps(data, indent=2)}\n")

            except websockets.exceptions.ConnectionClosed as e:
                print(f"[WebSocket] Connection closed: {e}")
                self.ws = None  # Clear the connection
                if self.should_reconnect:
                    print("[WebSocket] Will attempt to reconnect...")
                    await asyncio.sleep(1)
                else:
                    break
            except Exception as e:
                print(f"[WebSocket ERROR]: {e}")
                self.ws = None  # Clear the connection
                if self.should_reconnect:
                    print("[WebSocket] Will attempt to reconnect after error...")
                    await asyncio.sleep(5)
                else:
                    break

    async def run(self):
        """Main run loop"""
        await self.connect()

        # Run all tasks concurrently including keepalive
        send_task = asyncio.create_task(self.send_audio())
        receive_task = asyncio.create_task(self.receive_responses())
        tts_task = asyncio.create_task(self.play_audio_stream())
        ping_task = asyncio.create_task(self.keepalive_ping())

        try:
            await asyncio.gather(send_task, receive_task, tts_task, ping_task)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources"""
        self.is_recording = False
        self.should_reconnect = False

        if self.ws:
            try:
                asyncio.create_task(self.ws.close())
            except (RuntimeError, Exception):
                pass  # Loop may be closed or already closed

        if self.stream:
            self.stream.stop_stream()
            self.stream.close()

        if self.audio:
            self.audio.terminate()

        # Signal TTS to stop
        try:
            asyncio.create_task(self.text_queue.put(None))
        except RuntimeError:
            pass  # Loop may be closed

        print("Cleanup complete")


async def main():
    assistant = RealtimeAudioAssistant()
    await assistant.run()


if __name__ == "__main__":
    asyncio.run(main())
