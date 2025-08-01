from flask import Flask, Response
import requests
import os

app = Flask(__name__)

# Set your ElevenLabs API key in Render environment variables!
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")

GREETING = "Welcome to Fresh Fade Barbershop. How can I help you today?"

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
        os.makedirs("static", exist_ok=True)
        with open("static/response.mp3", "wb") as f:
            f.write(response.content)
        return True
    else:
        print("ElevenLabs Error:", response.text)
        return False

@app.route("/voice", methods=["POST"])
def voice():
    success = generate_speech(GREETING)
    if success and os.path.exists("static/response.mp3"):
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>https://ai-barber-appointment-receptionist.onrender.com/static/response.mp3</Play>
</Response>
"""
        return Response(twiml, mimetype="text/xml")
    else:
        fallback = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Sorry, an error has occurred. Please try again later.</Say>
</Response>
"""
        return Response(fallback, mimetype="text/xml")

@app.route("/", methods=["GET"])
def home():
    return "AI Barbershop with ElevenLabs is online."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
