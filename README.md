# Magoo Realtime Audio Assistant

A real-time voice AI assistant that uses OpenAI's Realtime API for speech-to-text transcription and Fish Audio for high-quality text-to-speech synthesis.

## Features

- **Real-time Speech Recognition**: Uses OpenAI's Realtime API with server-side VAD (Voice Activity Detection)
- **High-Quality TTS**: Fish Audio SDK for natural-sounding voice responses
- **Smart Microphone Muting**: Automatically mutes microphone during TTS playback to prevent audio feedback
- **WebSocket Keepalive**: Maintains stable long-running connections with automatic ping/pong
- **Streaming Audio Playback**: Real-time audio streaming using mpv for low-latency responses

## Requirements

- Python 3.8+
- PyAudio (requires PortAudio system library)
- mpv or mpg123 (for audio playback)

### System Dependencies

**Raspberry Pi / Debian / Ubuntu:**
```bash
sudo apt-get update
sudo apt-get install python3-pyaudio portaudio19-dev mpv
```

**macOS:**
```bash
brew install portaudio mpv
```

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
   ```

   - Get OpenAI API key: https://platform.openai.com/api-keys
   - Get Fish Audio API key: https://fish.audio/

## Usage

Run the assistant:
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

## How It Works

### Architecture

```
Microphone → OpenAI Realtime API → Text Response → Fish Audio TTS → Speaker
     ↑                                                                    ↓
     └──────────────── Muted during playback ──────────────────────────┘
```

### Key Components

1. **Audio Capture** (`send_audio`):
   - Captures audio at 24kHz PCM16 format
   - Sends to OpenAI Realtime API via WebSocket
   - Automatically sends silence when muted

2. **Response Processing** (`receive_responses`):
   - Receives events from OpenAI API
   - Extracts transcriptions and AI responses
   - Queues text for TTS processing

3. **Text-to-Speech** (`process_single_response`):
   - Mutes microphone before speaking
   - Streams audio chunks from Fish Audio
   - Plays audio through mpv
   - Unmutes microphone after completion

4. **Connection Management** (`keepalive_ping`):
   - Sends WebSocket pings every 10 seconds
   - Detects connection failures
   - Handles timeouts gracefully

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
# Audio configuration
CHUNK = 1024          # Audio buffer size
RATE = 24000          # Sample rate (24kHz required by OpenAI)
CHANNELS = 1          # Mono audio

# Session configuration (line 65-79)
"threshold": 0.5,              # VAD sensitivity (0.0-1.0)
"silence_duration_ms": 500     # Silence before end of speech

# TTS configuration (line 176-180)
format="mp3",                  # Audio format
latency="balanced",            # balanced/normal
chunk_length=150               # Characters per chunk
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
```bash
# Test microphone
arecord -l

# Test PyAudio
python -c "import pyaudio; p=pyaudio.PyAudio(); print(p.get_device_count())"
```

## Development

### Project Structure
```
magoo_realtime/
├── realtime_audio.py      # Main application
├── requirements.txt       # Python dependencies
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
