import os
from flask import Flask, request, Response
import requests

app = Flask(__name__)

# ENV VARIABLES (add these in Render dashboard!)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.environ.get("TWILIO_AUTH_TOKEN")

GREETING_MP3_URL = "https://ai-barber-appointment-receptionist.onrender.com/static/test.mp3"

@app.route("/", methods=["GET"])
def home():
    return "AI Barbershop with voice AI is online!"

@app.route("/voice", methods=["POST"])
def voice():
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Play>{GREETING_MP3_URL}</Play>
        <Record maxLength="10" action="/process_recording" playBeep="true" />
    </Response>
    """
    return Response(twiml, mimetype="text/xml")

@app.route("/process_recording", methods=["POST"])
def process_recording():
    # 1. Get recording URL from Twilio
    recording_url = request.form.get("RecordingUrl")
    if not recording_url:
        print("No RecordingUrl found in request.")
        return twiml_error("Sorry, there was a problem recording your message.")

    # 2. Download the audio file from Twilio (WAV by default) - NEED AUTH!
    audio_url = recording_url + ".wav"
    print("Downloading recording from:", audio_url)
    audio_data = requests.get(audio_url, auth=(TWILIO_SID, TWILIO_AUTH))
    if not audio_data.ok:
        print("Failed to download recording:", audio_data.text)
        return twiml_error("Sorry, there was a problem with your recording.")

    # 3. Send audio to OpenAI Whisper for transcription
    transcript = transcribe_audio(audio_data.content)
    print("Transcript:", transcript)

    # 4. Send transcript to ChatGPT, handle API errors gracefully
    reply = chatgpt_reply(transcript)
    print("ChatGPT reply:", reply)

    # 5. Synthesize reply with ElevenLabs
    mp3_url = synthesize_elevenlabs(reply)

    # 6. Play back the reply to caller
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Play>{mp3_url}</Play>
        <Pause length="1"/>
        <Say voice="alice">If you need more assistance, just call again. Goodbye!</Say>
        <Hangup/>
    </Response>
    """
    return Response(twiml, mimetype="text/xml")

def twiml_error(message):
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say>{message}</Say>
        <Hangup/>
    </Response>
    """
    return Response(twiml, mimetype="text/xml")

def transcribe_audio(audio_bytes):
    # Transcribe using OpenAI Whisper API
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
    if resp.ok and "text" in resp.json():
        return resp.json()["text"]
    else:
        print("Whisper API error:", resp.text)
        return "Sorry, I couldn't understand the recording."

def chatgpt_reply(transcript):
    # Query OpenAI ChatGPT with the transcript and error-handle the response
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
    try:
        data = response.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"].strip()
        else:
            print("OpenAI API error (no choices):", data)
            return "Sorry, there was a problem connecting to the AI. Please try again."
    except Exception as e:
        print("OpenAI API exception:", e)
        return "Sorry, the AI service is down right now."

def synthesize_elevenlabs(text):
    # Synthesize text using ElevenLabs API and save to static dir
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
        # Save MP3 to static dir
        reply_path = "static/ai_reply.mp3"
        with open(reply_path, "wb") as f:
            f.write(response.content)
        # Return public URL for Twilio <Play>
        return "https://ai-barber-appointment-receptionist.onrender.com/static/ai_reply.mp3"
    else:
        print("ElevenLabs error:", response.text)
        return GREETING_MP3_URL  # fallback

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
