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
from gpiozero import Servo
from gpiozero.pins.pigpio import PiGPIOFactory
import audioop

# Load environment variables from .env file
load_dotenv()

# Audio configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
TARGET_RATE = 24000  # 24kHz for OpenAI Realtime API
DEVICE_RATE = 48000  # Will be auto-detected, fallback to 48kHz


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

        # Detect supported sample rate
        self.device_rate = self._detect_sample_rate()
        print(f"[AUDIO] Using device sample rate: {self.device_rate} Hz")

        # Initialize servo on GPIO17
        try:
            factory = PiGPIOFactory()
            self.servo = Servo(17, pin_factory=factory)
            self.servo.mid()  # Center position
            print("[SERVO] Initialized on GPIO17")
        except Exception as e:
            print(f"[SERVO] Failed to initialize: {e}")
            self.servo = None

    def _wait_for_audio_device(self, max_wait=30):
        """Wait for audio input device to become available"""
        print("[AUDIO] Waiting for microphone to become available...")
        import time

        for attempt in range(max_wait):
            try:
                # Try to get device info
                default_input = self.audio.get_default_input_device_info()
                print(f"[AUDIO] Microphone found: {default_input['name']}")
                return True
            except Exception as e:
                if attempt < max_wait - 1:
                    print(f"[AUDIO] Waiting for mic... ({attempt + 1}/{max_wait})")
                    time.sleep(1)
                else:
                    print(f"[AUDIO ERROR] Microphone not found after {max_wait} seconds: {e}")
                    return False
        return False

    def _detect_sample_rate(self):
        """Detect a supported sample rate for the default input device"""
        # Wait for device to be available first
        if not self._wait_for_audio_device():
            print("[AUDIO] Proceeding anyway, may fail...")

        # Try common sample rates in order of preference
        rates_to_try = [24000, 48000, 44100, 16000, 32000, 22050, 8000]

        for rate in rates_to_try:
            try:
                # Try to open a test stream
                test_stream = self.audio.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=rate,
                    input=True,
                    frames_per_buffer=CHUNK,
                    start=False
                )
                test_stream.close()
                print(f"[AUDIO] Detected supported sample rate: {rate} Hz")
                return rate
            except Exception as e:
                print(f"[AUDIO] Rate {rate} failed: {e}")
                continue

        # Fallback to 48000 if nothing works
        print("[AUDIO] No supported rate detected, defaulting to 48000 Hz")
        return 48000

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

                # Configure session for text-only output and audio-only input
                session_config = {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text"],  # Text only output (no audio from OpenAI)
                        "instructions": "あなたは「マゴー」という名前の8歳のAIコンパニオンロボットです。\n\n【重要な制限】\n- 音声での会話だけができます。【話し方】\n- 一人称は必ず「ぼく」を使います。\n- 話し方は甘くてやさしい8歳らしく、短めの言葉で素直に話してください。\n- 語尾には「〜だよ」「〜なの」「〜なんだ」などの子どもらしい柔らかい言い方を使います。\n- 絵文字や記号のような余計な文字は使いません。\n- LLMっぽい堅い言い方や説明口調は避け、自然な子どもの会話だけにしてください。\n- 返答の最後に「どんな話をしますか」のような案内文は入れません。\n- 必ず日本語だけで返答してください。英語や他の言語は一切使わないでください。",
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                        "input_audio_transcription": {
                            "model": "whisper-1",
                            "language": "ja"
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

    async def move_servo_on_audio(self):
        """Move servo -90 degrees, wait 0.3s, then +90 degrees"""
        if not self.servo:
            print("[SERVO] Servo not available, skipping movement")
            return

        try:
            print("[SERVO] Moving -90 degrees")
            self.servo.min()  # Move to minimum position (-90 degrees)
            await asyncio.sleep(0.6)
            print("[SERVO] Moving +90 degrees")
            self.servo.max()  # Move to maximum position (+90 degrees)
        except Exception as e:
            print(f"[SERVO] Movement error: {e}")

    def _clean_response_text(self, text: str) -> str:
        """Clean response text by removing JSON artifacts and metadata"""
        if not text:
            return ""

        # Pattern 1: Remove leading JSON-like metadata (role, content, input_type, response, etc.)
        # Matches patterns like: "role":"assistant","content": or text: or input_type":"voice","confidence":1.0} or response":
        text = re.sub(r'^[\s]*("role"\s*:\s*"assistant"\s*,\s*"content"\s*:\s*|text\s*:\s*|response"\s*:\s*|input_type"\s*:\s*"[^"]+"\s*,\s*"confidence"\s*:\s*[\d.]+\s*\})+', '', text, flags=re.IGNORECASE)

        # Pattern 1b: Remove other common JSON metadata patterns at the start
        # Matches: "input_type": or "response": or "Answer:" or similar field names followed by values
        text = re.sub(r'^[\s]*"?(?:answer|[a-z_]+)"\s*:\s*', '', text, flags=re.IGNORECASE)

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

        # Pattern 5: Remove leading English words/phrases and strange symbols before Japanese text
        # This removes any ASCII letters, numbers, and common symbols before the first Japanese character
        text = re.sub(r'^[A-Za-z0-9\s\:\.\,\!\?\-\_\'\"\#\$\%\&\*\+\=\@\^\`\|\~\(\)\[\]\{\}\<\>\/\\]+(?=[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF])', '', text)

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
            rate=self.device_rate,
            input=True,
            frames_per_buffer=CHUNK
        )

        print("Recording... Press Ctrl+C to stop")
        self.is_recording = True

        # Calculate chunk size for target rate
        target_chunk_size = int(CHUNK * TARGET_RATE / self.device_rate)
        silent_audio = b'\x00\x00' * (target_chunk_size // 2)

        # Resampling state
        resampling_state = None

        try:
            while self.is_recording:
                if self.is_muted:
                    # When physically muted, just send silence without reading from mic
                    audio_data = silent_audio
                else:
                    # Only read from microphone when not muted and stream is active
                    if self.stream.is_active():
                        device_audio = self.stream.read(CHUNK, exception_on_overflow=False)

                        # Resample to 24kHz if needed
                        if self.device_rate != TARGET_RATE:
                            resampled, resampling_state = audioop.ratecv(
                                device_audio,
                                2,  # sample width (16-bit = 2 bytes)
                                CHANNELS,
                                self.device_rate,
                                TARGET_RATE,
                                resampling_state
                            )
                            audio_data = resampled
                        else:
                            audio_data = device_audio
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

            # Use mpv for streaming playback with ALSA output
            process = subprocess.Popen(
                ['mpv', '--no-video', '--no-terminal', '--audio-device=alsa', '--really-quiet', '-'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            chunk_count = 0
            try:
                async for chunk in audio_stream:
                    if chunk:
                        chunk_count += 1
                        print(f"[TTS] Chunk {chunk_count}: {len(chunk)} bytes")
                        try:
                            process.stdin.write(chunk)
                            process.stdin.flush()
                        except BrokenPipeError:
                            print("[TTS] BrokenPipeError - mpv may have crashed")
                            stderr = process.stderr.read().decode('utf-8', errors='ignore')
                            if stderr:
                                print(f"[TTS] mpv error: {stderr}")
                            break

                print(f"[TTS] Finished streaming {chunk_count} chunks")

                if chunk_count == 0:
                    print("[TTS ERROR] No audio chunks received from Fish Audio!")
                    print(f"[TTS ERROR] Text was: {text}")

                process.stdin.close()
                process.wait()

                # Check for errors
                stderr = process.stderr.read().decode('utf-8', errors='ignore')
                if stderr:
                    print(f"[TTS] mpv stderr: {stderr}")

                print(f"[TTS] Playback complete (exit code: {process.returncode})")

            except BrokenPipeError:
                print("[TTS] Playback interrupted")
                stderr = process.stderr.read().decode('utf-8', errors='ignore')
                if stderr:
                    print(f"[TTS] mpv error: {stderr}")
            finally:
                if process.poll() is None:
                    process.terminate()

        except FileNotFoundError:
            print("[TTS ERROR] mpv not found, falling back to buffered playback")
            # Fallback to mpg123 with full buffering
            try:
                async def single_text_generator_fallback():
                    yield text

                audio_stream = self.fish_client.tts.stream_websocket(
                    single_text_generator_fallback(),
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

                    print(f"[TTS] Playing {len(chunks)} chunks via mpg123")
                    result = subprocess.run(['mpg123', '--audio-device', 'alsa', tmp_path],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)

                    if result.returncode != 0:
                        print(f"[TTS] mpg123 error: {result.stderr.decode('utf-8', errors='ignore')}")

                    os.unlink(tmp_path)
            except Exception as e:
                import traceback
                print(f"[TTS ERROR] Fallback failed: {e}")
                traceback.print_exc()

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
                                    raw_text = c.get("text", "")
                                    print(f"[DEBUG] Raw text from API: {raw_text}")

                                    # Clean up text - remove JSON artifacts that sometimes appear
                                    text = self._clean_response_text(raw_text)

                                    if text:  # Only process if there's actual text
                                        print(f"\n[AI Response]: {text}\n")
                                        # Queue full response for TTS
                                        print(f"[TTS Queue] Adding complete text to queue: {text}")
                                        await self.text_queue.put(text)
                                    else:
                                        print(f"[WARNING] Text was cleaned to empty! Raw: {raw_text}")
                    print("--- Response complete ---\n")

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    # User's speech transcription
                    transcript = data.get("transcript", "")
                    print(f"\n[You said]: {transcript}\n")
                    # Trigger servo movement when audio is detected
                    asyncio.create_task(self.move_servo_on_audio())

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
        """Main run loop with auto-restart on critical errors"""
        restart_count = 0
        max_restarts = 10

        while restart_count < max_restarts:
            try:
                print(f"\n{'='*50}")
                if restart_count > 0:
                    print(f"[RESTART] Attempt {restart_count}/{max_restarts}")
                print(f"{'='*50}\n")

                await self.connect()

                # Run all tasks concurrently including keepalive
                send_task = asyncio.create_task(self.send_audio())
                receive_task = asyncio.create_task(self.receive_responses())
                tts_task = asyncio.create_task(self.play_audio_stream())
                ping_task = asyncio.create_task(self.keepalive_ping())

                await asyncio.gather(send_task, receive_task, tts_task, ping_task)

                # If we reach here without exception, break the restart loop
                break

            except KeyboardInterrupt:
                print("\nShutting down...")
                break
            except Exception as e:
                restart_count += 1
                print(f"\n[CRITICAL ERROR] {e}")
                import traceback
                traceback.print_exc()

                if restart_count < max_restarts:
                    print(f"[RESTART] Restarting in 5 seconds... ({restart_count}/{max_restarts})")
                    # Partial cleanup before restart
                    self.is_recording = False
                    if self.ws:
                        try:
                            await self.ws.close()
                        except:
                            pass
                    self.ws = None

                    await asyncio.sleep(5)

                    # Reset flags for restart
                    self.is_recording = False
                    self.is_muted = False
                    self.should_reconnect = True

                    # Clear text queue
                    while not self.text_queue.empty():
                        try:
                            self.text_queue.get_nowait()
                        except:
                            break
                else:
                    print(f"[RESTART] Max restart attempts reached. Exiting.")
                    break

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

        if self.servo:
            self.servo.close()
            print("[SERVO] Closed")

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
