from flask import Flask, request, Response
import requests
import os

app = Flask(__name__)

# --- CONFIGURATION ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
RENDER_URL = "https://ai-barber-appointment-receptionist.onrender.com"

GREETING_MP3_URL = f"{RENDER_URL}/static/test.mp3"

# --- MAIN GREETING/RECORD ---
@app.route("/voice", methods=["POST"])
def voice():
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{GREETING_MP3_URL}</Play>
    <Record maxLength="10" action="/process_recording" playBeep="true" />
</Response>
"""
    return Response(twiml, mimetype="text/xml")

# --- HANDLE RECORDING & AI REPLY ---
@app.route("/process_recording", methods=["POST"])
def process_recording():
    recording_url = request.form.get("RecordingUrl")
    if not recording_url:
        return Response(error_twiml("Sorry, there was a problem with your recording."), mimetype="text/xml")

    # Download the caller's audio
    audio = requests.get(recording_url + ".mp3")
    with open("static/caller.mp3", "wb") as f:
        f.write(audio.content)

    # Transcribe with OpenAI Whisper API
    transcript = transcribe_audio("static/caller.mp3")

    # Get a smart reply from ChatGPT
    reply = chatgpt_reply(transcript)

    # Synthesize reply with ElevenLabs
    mp3_path = generate_speech(reply)

    # TwiML to play AI reply
    ai_reply_url = f"{RENDER_URL}/static/response.mp3"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{ai_reply_url}</Play>
</Response>
"""
    return Response(twiml, mimetype="text/xml")

# --- UTILITY: Transcribe Audio using OpenAI Whisper ---
def transcribe_audio(audio_path):
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }
    files = {
        "file": open(audio_path, "rb"),
        "model": (None, "whisper-1"),
        "response_format": (None, "text")
    }
    response = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers=headers,
        files=files
    )
    transcript = response.text.strip()
    print(f"[TRANSCRIPT] {transcript}")
    return transcript

# --- UTILITY: Get AI reply from ChatGPT ---
def chatgpt_reply(prompt):
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are a friendly, professional barbershop receptionist. Greet callers, answer basic questions, and help with bookings."},
            {"role": "user", "content": prompt}
        ]
    }
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=data
    )
    reply = response.json()["choices"][0]["message"]["content"].strip()
    print(f"[AI REPLY] {reply}")
    return reply

# --- UTILITY: Generate speech with ElevenLabs ---
def generate_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.75
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        with open("static/response.mp3", "wb") as f:
            f.write(response.content)
        return "static/response.mp3"
    else:
        print("ElevenLabs Error:", response.text)
        return None

# --- UTILITY: TwiML for Error ---
def error_twiml(msg):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">{msg}</Say>
</Response>
"""

@app.route("/", methods=["GET"])
def home():
    return "AI Barbershop with voice AI is online!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
