AI Companion Robot - Realtime Audio System
==========================================

Setup:
1. Create virtual environment: python3 -m venv venv
2. Activate virtual environment: source venv/bin/activate
3. Install dependencies: pip install -r requirements.txt
4. Copy .env.example to .env and add your OpenAI API key
5. Run: python realtime_audio.py

The system will:
- Capture audio from your microphone
- Send it to OpenAI's Realtime API
- Return text responses only (no audio generation)
- Show transcription of your speech
- Display the AI's text response

Press Ctrl+C to stop.
