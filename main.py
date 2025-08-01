from flask import Flask, request, Response
import os
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# Load environment variables
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")
SERVICE_ACCOUNT_FILE = "service_account.json"

# Check calendar availability
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
    return len(events) == 0

# Create a booking
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

# Twilio voice endpoint using public MP3
@app.route("/voice", methods=["POST"])
def voice():
    response = """<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Play>https://www2.cs.uic.edu/~i101/SoundFiles/StarWars60.wav</Play>
    </Response>
    """.strip()
    return Response(response, mimetype="text/xml")

# Health check
@app.route("/", methods=["GET"])
def index():
    return "AI Barbershop Receptionist is online (using test audio)."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
