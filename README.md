# Magoo Realtime Audio Assistant

A real-time voice AI assistant that uses OpenAI's Realtime API for speech-to-text transcription and Fish Audio for high-quality text-to-speech synthesis.

## Features

- **Real-time Speech Recognition**: Uses OpenAI's Realtime API with server-side VAD (Voice Activity Detection)
- **High-Quality TTS**: Fish Audio SDK for natural-sounding voice responses
- **Smart Microphone Muting**: Automatically mutes microphone during TTS playback to prevent audio feedback
- **WebSocket Keepalive**: Maintains stable long-running connections with automatic ping/pong
- **Streaming Audio Playback**: Real-time audio streaming using mpv with ALSA for low-latency responses
- **Servo Control**: GPIO-based servo movement synchronized with audio input (Raspberry Pi)
- **Auto Sample Rate Detection**: Automatically detects and adapts to supported audio device sample rates
- **Auto-Start on Boot**: Systemd service for running on Raspberry Pi startup
- **Automatic Crash Recovery**: Multi-layer restart system handles network failures, crashes, and system errors
- **Smart Microphone Detection**: Waits up to 30 seconds for USB audio devices to initialize on boot
- **Audio-Only Mode**: Configured for voice-only interaction (no image input)

## Requirements

- Python 3.8+
- PyAudio (requires PortAudio system library)
- mpv or mpg123 (for audio playback)
- gpiozero and pigpio (for servo control on Raspberry Pi)

### System Dependencies

**Raspberry Pi / Debian / Ubuntu:**
```bash
sudo apt-get update
sudo apt-get install python3-pyaudio portaudio19-dev mpv pigpio
sudo systemctl enable pigpiod
sudo systemctl start pigpiod
```

**macOS:**
```bash
brew install portaudio mpv
```

Note: GPIO features are Raspberry Pi only.

## Installation

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd magoo_realtime
   ```

2. **Create and activate virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure API keys:**
   ```bash
   cp .env.example .env
   ```

   Edit `.env` and add your API keys:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   FISH_API_KEY=your_fish_audio_api_key_here
   FISH_VOICE_ID=your_fish_voice_id_here
   ```

   - Get OpenAI API key: https://platform.openai.com/api-keys
   - Get Fish Audio API key: https://fish.audio/
   - Get Fish Voice ID from your Fish Audio dashboard

## Usage

### Manual Mode

Run the assistant manually:
```bash
python realtime_audio.py
```

The system will:
1. Connect to OpenAI's Realtime API
2. Start listening to your microphone
3. Transcribe your speech in real-time
4. Generate text responses from the AI
5. Convert responses to speech using Fish Audio
6. Automatically mute the microphone during TTS playback

**To stop:** Press `Ctrl+C`

### Auto-Start on Boot (Raspberry Pi)

To make Magoo start automatically when the Raspberry Pi powers on:

1. **Copy the service file to systemd:**
   ```bash
   sudo cp magoo.service /etc/systemd/system/
   ```

2. **Reload systemd:**
   ```bash
   sudo systemctl daemon-reload
   ```

3. **Enable the service:**
   ```bash
   sudo systemctl enable magoo.service
   ```

4. **Start the service now:**
   ```bash
   sudo systemctl start magoo.service
   ```

5. **Check status:**
   ```bash
   sudo systemctl status magoo.service
   ```

6. **View logs:**
   ```bash
   sudo journalctl -u magoo.service -f
   ```

**To stop the service:**
```bash
sudo systemctl stop magoo.service
```

**To disable auto-start:**
```bash
sudo systemctl disable magoo.service
```

**Service Features:**
- Waits 15 seconds after boot for USB audio devices to initialize
- Waits up to 30 seconds for microphone to be detected (Python-level)
- Uses ALSA for direct audio device access
- Auto-restarts on crashes (5 attempts in 60 seconds max)
- Auto-restarts on network failures (Python reconnection + systemd restart)
- Graceful shutdown with 15-second timeout
- Logs all output to systemd journal with identifier "magoo"

## How It Works

### Architecture

```
Microphone → OpenAI Realtime API → Text Response → Fish Audio TTS → Speaker
     ↑                                                                    ↓
     └──────────────── Muted during playback ──────────────────────────┘
```

### Key Components

1. **Audio Initialization** (`_wait_for_audio_device`, `_detect_sample_rate`):
   - Waits up to 30 seconds for microphone to appear
   - Auto-detects supported sample rate (24kHz, 48kHz, 44.1kHz, etc.)
   - Displays detected microphone device name

2. **Audio Capture** (`send_audio`):
   - Resamples to 24kHz PCM16 for OpenAI Realtime API if needed
   - Sends to OpenAI Realtime API via WebSocket (audio-only, no image input)
   - Automatically sends silence when muted

3. **Response Processing** (`receive_responses`):
   - Receives events from OpenAI API
   - Extracts transcriptions and AI responses
   - Cleans response text (removes JSON artifacts)
   - Queues text for TTS processing

4. **Text-to-Speech** (`process_single_response`):
   - Mutes microphone before speaking
   - Streams audio chunks from Fish Audio
   - Plays audio through mpv using ALSA (falls back to mpg123 if mpv unavailable)
   - Unmutes microphone after completion

5. **Connection Management** (`keepalive_ping`, `connect`):
   - Sends WebSocket pings every 10 seconds
   - Auto-reconnects on connection loss with exponential backoff
   - Handles timeouts gracefully

6. **Crash Recovery** (`run`):
   - Catches all unhandled exceptions
   - Restarts entire session up to 10 times
   - Cleans up resources before restart
   - Works with systemd for process-level recovery

### Microphone Muting

The system physically mutes the microphone during TTS playback by:
- Stopping the audio input stream (`stream.stop_stream()`)
- Sending silence packets to the API
- Adding 0.2s delay before TTS starts
- Adding 0.5s delay after TTS ends
- Restarting the stream when done

This prevents the assistant from hearing its own voice and creating a feedback loop.

## Configuration

Edit these values in `realtime_audio.py`:

```python
# Audio configuration (lines 24-28)
CHUNK = 1024          # Audio buffer size
TARGET_RATE = 24000   # Sample rate for OpenAI (24kHz required)
DEVICE_RATE = 48000   # Auto-detected from your microphone
CHANNELS = 1          # Mono audio

# Session configuration (lines 121-139)
"modalities": ["text"],        # Text-only output (no audio from OpenAI)
"threshold": 0.5,              # VAD sensitivity (0.0-1.0)
"silence_duration_ms": 500     # Silence before end of speech

# TTS configuration (lines 339-343)
format="mp3",                  # Audio format
latency="balanced",            # balanced/normal
chunk_length=150               # Characters per chunk

# Microphone detection (line 70)
max_wait=30                    # Seconds to wait for microphone on boot

# Crash recovery (line 572)
max_restarts=10                # Maximum restart attempts
```

## Troubleshooting

### "ALSA lib ... Underrun occurred"
This is normal and can be ignored. It's an ALSA audio buffer warning.

### "mpv not found"
Install mpv: `sudo apt-get install mpv` or the system will fall back to mpg123.

### Connection timeouts
- Check your internet connection
- Verify API keys are correct
- The system auto-reconnects on failure

### Microphone not detected

The system automatically waits up to 30 seconds for the microphone to be detected. If you still have issues:

```bash
# Test microphone
arecord -l

# Test PyAudio
python -c "import pyaudio; p=pyaudio.PyAudio(); print(p.get_device_count())"

# Check service logs to see microphone detection
sudo journalctl -u magoo -n 50 | grep AUDIO
```

You should see:
```
[AUDIO] Waiting for microphone to become available...
[AUDIO] Microphone found: <device name>
[AUDIO] Detected supported sample rate: 48000 Hz
```

If the microphone is never found:
- Ensure USB audio device is properly connected
- Try a different USB port
- Check if device appears with `lsusb`
- The service will auto-restart up to 5 times to retry detection

### "OSError: [Errno -9999] Unanticipated host error" or "Invalid sample rate" (Raspberry Pi)
The application automatically detects supported sample rates and uses ALSA for audio playback. If you encounter issues:

1. **Verify audio devices are accessible:**
   ```bash
   arecord -l  # List capture devices
   aplay -l    # List playback devices
   ```

2. **Test audio playback:**
   ```bash
   speaker-test -t wav -c 2
   ```

3. **Check service logs for detected sample rate:**
   ```bash
   sudo journalctl -u magoo.service -f
   ```

   You should see: `[AUDIO] Detected supported sample rate: XXXXX Hz`

### Servo not working (Raspberry Pi)
1. **Check pigpio daemon is running:**
   ```bash
   sudo systemctl status pigpiod
   ```

2. **Start pigpio daemon:**
   ```bash
   sudo systemctl start pigpiod
   ```

3. **Enable pigpio daemon on boot:**
   ```bash
   sudo systemctl enable pigpiod
   ```

### Service crashes or stops unexpectedly

The system has multi-layer automatic crash recovery:

**Python-level recovery:**
- Automatically reconnects WebSocket on connection loss
- Retries up to 10 times with exponential backoff (max 5 minutes between attempts)
- Restarts entire session on critical errors

**Systemd-level recovery:**
- Restarts process automatically on crashes
- Maximum 5 restart attempts within 60 seconds
- Waits 5 seconds between restart attempts

**To check what happened:**
```bash
# View recent logs
sudo journalctl -u magoo -n 200

# View live logs
sudo journalctl -u magoo -f

# Check service status
sudo systemctl status magoo
```

**If service is stopped after multiple crashes:**
```bash
# Reset failure counter and restart
sudo systemctl reset-failed magoo
sudo systemctl restart magoo
```

**Common error patterns in logs:**
- `[WebSocket] Connection failed` - Network issues, auto-retries
- `[TTS ERROR] No audio chunks` - Fish Audio API issue
- `[AUDIO ERROR] Microphone not found` - USB audio device not ready
- `[CRITICAL ERROR]` - Unhandled exception, systemd will restart

## Development

### Project Structure
```
magoo_realtime/
├── realtime_audio.py      # Main application
├── requirements.txt       # Python dependencies
├── magoo.service          # Systemd service for auto-start
├── .env                   # API keys (not in git)
├── .env.example          # Template for API keys
├── .gitignore            # Git ignore rules
└── README.md             # This file
```

### Testing TTS Only
See `test_tts_simple.py` for standalone TTS testing.

## Safety & Privacy

- **Never commit `.env`** - it contains your API keys
- All API calls go through HTTPS/WSS encrypted connections
- Audio is processed in real-time and not stored locally
- Check OpenAI and Fish Audio privacy policies for cloud processing details

## License

[Add your license here]

## Contributing

[Add contribution guidelines here]

## Credits

- OpenAI Realtime API: https://platform.openai.com/docs/guides/realtime
- Fish Audio SDK: https://docs.fish.audio/
