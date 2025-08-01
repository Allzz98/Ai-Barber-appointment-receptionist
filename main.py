from flask import Flask, request, Response
import openai
import requests
import json
import os
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# Load environment variables
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
SERVICE_ACCOUNT_FILE = "service_account.json"

openai.api_key = OPENAI_API_KEY

# Check if requested time is available on Google Calendar
def check_availability(requested_datetime):
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    service = build("calendar", "v3", credentials=credentials)

    start = requested_datetime.isoformat() + 'Z'
    end = (requested_datetime + datetime.timedelta(hours=1)).isoformat() + 'Z'

    events_result = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=start,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])
    return len(events) == 0  # True if no events found

# Create a booking on Google Calendar
def create_booking(name, description, start_time):
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    service = build("calendar", "v3", credentials=credentials)

    event = {
        'summary': f"Haircut - {name}",
        'description': description,
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Australia/Sydney'},
        'end': {'dateTime': (start_time + datetime.timedelta(hours=1)).isoformat(), 'timeZone': 'Australia/Sydney'}
    }

    created_event = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
    return created_event.get("htmlLink")

# Convert text to MP3 using ElevenLabs
def generate_speech(text):
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "text": text,
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.7}
        }
    )
    with open("static/response.mp3", "wb") as f:
        f.write(response.content)
    return "/static/response.mp3"

# Twilio voice endpoint
@app.route("/voice", methods=["POST"])
def voice():
    reply = "Welcome to Fresh Fade Barbershop. How can I assist you today?"
    mp3_path = generate_speech(reply)

    response = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Play>https://ai-barber-appointment-receptionist.onrender.com{mp3_path}</Play>
    </Response>
    """.strip()
    return Response(response, mimetype="text/xml")

# Health check
@app.route("/", methods=["GET"])
def index():
    return "AI Barbershop Receptionist is online."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
