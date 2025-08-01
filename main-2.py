
from flask import Flask, request, Response
import requests
import os

app = Flask(__name__)

# ElevenLabs setup
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # Default voice

# Text to speak
GREETING_TEXT = "Welcome to Fresh Fade Barbershop. How can I assist you today?"

# Generate speech using ElevenLabs
def generate_speech(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "text": text,
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.75
        }
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        audio_path = "static/response.mp3"
        with open(audio_path, "wb") as f:
            f.write(response.content)
        return audio_path
    else:
        print("ElevenLabs Error:", response.text)
        return None

@app.route("/", methods=["GET"])
def home():
    return "AI Barbershop is online (ElevenLabs powered)."

@app.route("/voice", methods=["POST"])
def voice():
    mp3_path = generate_speech(GREETING_TEXT)
    if mp3_path and os.path.exists(mp3_path):
        response = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Play>https://ai-barber-appointment-receptionist.onrender.com/static/response.mp3</Play>
        </Response>
        """
        return Response(response.strip(), mimetype="text/xml")
    else:
        fallback = """<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="alice">We are sorry. An error has occurred. Please try again later.</Say>
        </Response>
        """
        return Response(fallback, mimetype="text/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
