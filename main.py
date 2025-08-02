import os
import sys
from flask import Flask, request, Response
import requests

sys.stdout.reconfigure(line_buffering=True)  # Flush logs immediately

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.environ.get("TWILIO_AUTH_TOKEN")

GREETING_MP3_URL = "https://ai-barber-appointment-receptionist.onrender.com/static/test.mp3"

@app.route("/", methods=["GET"])
def home():
    print("Home page requested")
    sys.stdout.flush()
    return "AI Barbershop with unlimited voice AI is online!"

@app.route("/voice", methods=["POST"])
def voice():
    ai_reply_url = request.form.get("ai_reply_url")
    print("Voice endpoint hit; ai_reply_url:", ai_reply_url)
    sys.stdout.flush()

    if ai_reply_url:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{ai_reply_url}</Play>
    <Record maxLength="10" action="/process_recording" playBeep="true" timeout="5" />
</Response>
"""
    else:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{GREETING_MP3_URL}</Play>
    <Record maxLength="10" action="/process_recording" playBeep="true" timeout="5" />
</Response>
"""
    print("Responding with voice/record TwiML")
    sys.stdout.flush()
    return Response(twiml, mimetype="text/xml")

@app.route("/process_recording", methods=["POST"])
def process_recording():
    print("process_recording endpoint hit")
    print("Request form data:", request.form)
    sys.stdout.flush()

    recording_url = request.form.get("RecordingUrl")
    if not recording_url:
        print("No RecordingUrl found in request.")
        sys.stdout.flush()
        return twiml_error("Sorry, there was a problem recording your message.")

    audio_url = recording_url + ".wav"
    print("Downloading recording from:", audio_url)
    sys.stdout.flush()

    audio_data = requests.get(audio_url, auth=(TWILIO_SID, TWILIO_AUTH))
    if not audio_data.ok:
        print("Failed to download recording:", audio_data.text)
        sys.stdout.flush()
        return twiml_error("Sorry, there was a problem with your recording.")

    transcript = transcribe_audio(audio_data.content)
    print("Transcript:", transcript)
    sys.stdout.flush()

    reply = chatgpt_reply(transcript)
    print("ChatGPT reply:", reply)
    sys.stdout.flush()

    mp3_url = synthesize_elevenlabs(reply)
    print("Synthesized speech URL:", mp3_url)
    sys.stdout.flush()

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Redirect method="POST">/voice?ai_reply_url={mp3_url}</Redirect>
</Response>
"""
    print("Sending Redirect TwiML with new AI reply")
    sys.stdout.flush()
    return Response(twiml, mimetype="text/xml")

def twiml_error(message):
    print("Twiml error response:", message)
    sys.stdout.flush()
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>{message}</Say>
    <Hangup/>
</Response>
"""
    return Response(twiml, mimetype="text/xml")

def transcribe_audio(audio_bytes):
    files = {'file': ('audio.wav', audio_bytes, 'audio/wav')}
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    data = {'model': 'whisper-1'}
    resp = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers=headers,
        files=files,
        data=data,
    )
    print("Whisper response:", resp.text)
    sys.stdout.flush()
    if resp.ok and "text" in resp.json():
        return resp.json()["text"]
    else:
        print("Whisper API error:", resp.text)
        sys.stdout.flush()
        return "Sorry, I couldn't understand the recording."

def chatgpt_reply(transcript):
    api_url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are an AI receptionist for a barbershop. Be helpful, friendly, and book appointments if asked."},
            {"role": "user", "content": transcript}
        ]
    }
    response = requests.post(api_url, headers=headers, json=data)
    print("OpenAI response:", response.text)
    sys.stdout.flush()
    try:
        data = response.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"].strip()
        else:
            print("OpenAI API error (no choices):", data)
            sys.stdout.flush()
            return "Sorry, there was a problem connecting to the AI. Please try again."
    except Exception as e:
        print("OpenAI API exception:", e)
        sys.stdout.flush()
        return "Sorry, the AI service is down right now."

def synthesize_elevenlabs(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.3, "similarity_boost": 0.7}
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.ok:
        reply_path = "static/ai_reply.mp3"
        with open(reply_path, "wb") as f:
            f.write(response.content)
        return "https://ai-barber-appointment-receptionist.onrender.com/static/ai_reply.mp3"
    else:
        print("ElevenLabs error:", response.text)
        sys.stdout.flush()
        return GREETING_MP3_URL  # fallback

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
