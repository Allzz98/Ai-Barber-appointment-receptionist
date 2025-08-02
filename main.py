import os
import sys
import json
import datetime
from flask import Flask, request, Response
import requests
from dateutil import parser as dateparser
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Immediate log flushing
sys.stdout.reconfigure(line_buffering=True)

app = Flask(__name__)

# === CONFIG / ENV VARS ===
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.environ.get("TWILIO_AUTH_TOKEN")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")
BASE_URL = os.environ.get("BASE_URL")  # optional override, e.g. https://your-app.onrender.com

# Calendar service account JSON handling
SERVICE_ACCOUNT_FILENAME = "barber-shop-ai-booking-system-1daece25cca2.json"
if not os.path.exists(SERVICE_ACCOUNT_FILENAME):
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        with open(SERVICE_ACCOUNT_FILENAME, "w") as f:
            f.write(sa_json)

# Timezone for event creation
TIMEZONE = "Australia/Brisbane"  # adjust if needed

# Greeting audio (must exist in static)
GREETING_MP3_PATH = "/static/test.mp3"

# In-memory context per call (lost on restart)
contexts: dict[str, dict] = {}

# === HELPERS ===

def get_base_url():
    if BASE_URL:
        return BASE_URL.rstrip("/")
    # fallback from request if available
    return request.url_root.strip("/")

def get_calendar_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILENAME,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return service

def is_slot_available(requested_dt: datetime.datetime, duration_minutes=60):
    service = get_calendar_service()
    # Google expects RFC3339 with timezone offset; convert to ISO with timezone
    start = requested_dt.isoformat()
    end = (requested_dt + datetime.timedelta(minutes=duration_minutes)).isoformat()
    try:
        events_result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])
        return len(events) == 0
    except Exception as e:
        print("Calendar availability check error:", e)
        return False  # assume not available on failure

def find_next_available(start_dt: datetime.datetime, duration_minutes=60, max_tries=8):
    # Try advancing by slot increments (e.g., 30 mins) to find next open
    candidate = start_dt
    for i in range(max_tries):
        if is_slot_available(candidate, duration_minutes):
            return candidate
        candidate += datetime.timedelta(minutes=30)
    return None

def create_booking(name: str, description: str, start_dt: datetime.datetime, duration_minutes=60):
    service = get_calendar_service()
    event = {
        "summary": f"Haircut - {name}",
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": (start_dt + datetime.timedelta(minutes=duration_minutes)).isoformat(), "timeZone": TIMEZONE},
    }
    created = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
    return created.get("htmlLink")

def transcribe_audio(audio_bytes):
    if not OPENAI_API_KEY:
        print("Missing OpenAI API key for transcription.")
        return "Sorry, I can't access the AI right now."
    files = {'file': ('audio.wav', audio_bytes, 'audio/wav')}
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    data = {'model': 'whisper-1'}
    try:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers,
            files=files,
            data=data,
            timeout=30
        )
        print("Whisper response:", resp.text)
        sys.stdout.flush()
        json_data = resp.json()
        if resp.ok and "text" in json_data:
            return json_data["text"]
        else:
            print("Whisper API error:", json_data)
            return "Sorry, I couldn't understand the recording."
    except Exception as e:
        print("Exception during transcription:", e)
        return "Sorry, I couldn't process your voice right now."

def chatgpt_parse_and_respond(call_sid: str, user_message: str):
    """
    Maintains context and asks ChatGPT for next action.
    Expects structured JSON reply from the model describing intent / slots and reply_text.
    """
    if not OPENAI_API_KEY:
        return "Sorry, AI is not configured.", {}

    # Retrieve or initialize context
    ctx = contexts.setdefault(call_sid, {
        "name": None,
        "requested_datetime": None,
        "service": None,
        "booking_confirmed": False,
        "awaiting_confirmation": False,
        "last_reply": None
    })

    system_prompt = (
        "You are a smart barbershop receptionist. Keep the conversation natural, "
        "and help callers book haircuts. "
        "Track these slots: name, desired date/time, service. "
        "If the caller expresses intent to book, ask missing pieces one at a time. "
        "After you have name, datetime, and service, check availability is done by the backend. "
        "If the slot is busy, propose the next available slot if provided by backend. "
        "Only when the user explicitly confirms the booking, respond with a field booking_confirmed true. "
        "Output a JSON object with these keys: "
        "\"reply_text\" (what to say), "
        "\"name\" (if provided or known), "
        "\"requested_datetime\" (ISO 8601 if user gave a date/time, else null), "
        "\"service\" (if provided), "
        "\"need_confirmation\" (true if you need the user to confirm a booking), "
        "\"booking_intent\" (true if user wants to book), "
        "\"booking_confirmed\" (true if user said confirm), "
        "\"ask_for\" (one of 'name','datetime','service','confirmation', or null). "
        "Keep reply_text concise and friendly."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # Provide current known context to model so it doesn't forget
    if ctx["name"]:
        messages.append({"role": "user", "content": f"Current name on file: {ctx['name']}"})
    if ctx["requested_datetime"]:
        messages.append({"role": "user", "content": f"Current requested datetime on file: {ctx['requested_datetime']}"})
    if ctx["service"]:
        messages.append({"role": "user", "content": f"Current service on file: {ctx['service']}"})

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 400
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=30
        )
        print("ChatGPT raw response:", resp.text)
        sys.stdout.flush()
        data = resp.json()
        if "choices" in data and len(data["choices"]) > 0:
            content = data["choices"][0]["message"]["content"].strip()
            # Expecting JSON object; try to parse
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                # Fallback: wrap in minimal reply
                print("Failed to parse JSON from ChatGPT reply. Falling back. Content:", content)
                parsed = {
                    "reply_text": content,
                    "name": ctx["name"],
                    "requested_datetime": ctx["requested_datetime"],
                    "service": ctx["service"],
                    "need_confirmation": False,
                    "booking_intent": False,
                    "booking_confirmed": False,
                    "ask_for": None
                }
            # Update context with any provided slots
            if parsed.get("name"):
                ctx["name"] = parsed["name"]
            if parsed.get("requested_datetime"):
                # normalize/validate datetime
                try:
                    dt = dateparser.parse(parsed["requested_datetime"])
                    ctx["requested_datetime"] = dt.isoformat()
                except Exception:
                    pass
            if parsed.get("service"):
                ctx["service"] = parsed["service"]
            if parsed.get("booking_confirmed"):
                ctx["booking_confirmed"] = True
            if parsed.get("need_confirmation"):
                ctx["awaiting_confirmation"] = True

            ctx["last_reply"] = parsed.get("reply_text")
            return parsed.get("reply_text", ""), parsed
        else:
            print("ChatGPT missing choices or unexpected structure:", data)
            return "Sorry, I had trouble thinking right now.", {}
    except Exception as e:
        print("ChatGPT exception:", e)
        return "Sorry, the AI service is unavailable right now.", {}

def synthesize_elevenlabs(text: str) -> str:
    if not ELEVENLABS_API_KEY:
        print("Missing ElevenLabs API key.")
        sys.stdout.flush()
        return f"{get_base_url()}{GREETING_MP3_PATH}"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "voice_settings": {"stability": 0.3, "similarity_boost": 0.7}
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.ok:
            reply_path = "static/ai_reply.mp3"
            with open(reply_path, "wb") as f:
                f.write(response.content)
            final_url = f"{get_base_url()}/static/ai_reply.mp3"
            return final_url
        else:
            print("ElevenLabs error:", response.text)
            return f"{get_base_url()}{GREETING_MP3_PATH}"
    except Exception as e:
        print("Exception during ElevenLabs call:", e)
        return f"{get_base_url()}{GREETING_MP3_PATH}"

def twiml_error(message: str):
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>{message}</Say>
    <Hangup/>
</Response>
"""
    return Response(twiml, mimetype="text/xml")

# === ROUTES ===

@app.route("/", methods=["GET"])
def home():
    print("Health check / home called")
    sys.stdout.flush()
    return "AI Barbershop receptionist is online."

@app.route("/voice", methods=["POST"])
def voice():
    ai_reply_url = request.values.get("ai_reply_url")
    print("Voice endpoint hit; ai_reply_url:", ai_reply_url)
    sys.stdout.flush()

    if ai_reply_url:
        # play last AI reply then record next utterance
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{ai_reply_url}</Play>
    <Record maxLength="15" action="/process_recording" playBeep="true" timeout="5" />
</Response>
"""
    else:
        # initial greeting
        greeting_url = f"{get_base_url()}{GREETING_MP3_PATH}"
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{greeting_url}</Play>
    <Say voice="alice">Welcome to Fresh Fade Barbershop. How can I help you today?</Say>
    <Record maxLength="15" action="/process_recording" playBeep="true" timeout="5" />
</Response>
"""
    return Response(twiml, mimetype="text/xml")

@app.route("/process_recording", methods=["POST"])
def process_recording():
    print("process_recording invoked; form data:", request.form)
    sys.stdout.flush()

    call_sid = request.form.get("CallSid") or request.form.get("CallSid", "")
    recording_url = request.form.get("RecordingUrl")
    if not recording_url:
        return twiml_error("I didn't get your voice. Please try again.")

    # Download Twilio recording (no .wav append)
    print("Downloading recording from:", recording_url)
    sys.stdout.flush()
    audio_resp = requests.get(recording_url, auth=(TWILIO_SID, TWILIO_AUTH))
    if not audio_resp.ok:
        print("Failed to fetch recording:", audio_resp.text)
        sys.stdout.flush()
        return twiml_error("Sorry, I couldn't retrieve your message.")

    # Transcribe
    transcript = transcribe_audio(audio_resp.content)
    print("Transcribed user:", transcript)
    sys.stdout.flush()

    # Use ChatGPT to decide next step and get structured reply
    reply_text, parsed = chatgpt_parse_and_respond(call_sid, transcript)

    # Booking logic injection if intent present
    ctx = contexts.get(call_sid, {})
    booking_link = None
    if parsed.get("booking_intent"):
        # If user provided a datetime string
        requested_iso = ctx.get("requested_datetime")
        requested_dt = None
        if requested_iso:
            try:
                requested_dt = dateparser.parse(requested_iso)
            except Exception:
                requested_dt = None

        # If we have a datetime and not yet confirmed
        if requested_dt and not ctx.get("booking_confirmed"):
            available = is_slot_available(requested_dt)
            if available:
                # Ask confirmation if not yet asked
                if not ctx.get("awaiting_confirmation"):
                    reply_text = f"Got it. I can book you for {requested_dt.strftime('%A %I:%M %p')}. Should I confirm this appointment?"
                    ctx["awaiting_confirmation"] = True
                else:
                    # If user confirmed (booking_confirmed was set by GPT), create booking
                    if ctx.get("booking_confirmed"):
                        name = ctx.get("name") or "Client"
                        service = ctx.get("service") or "Haircut"
                        booking_link = create_booking(name, service, requested_dt)
                        reply_text = (
                            f"Booking confirmed for {requested_dt.strftime('%A %I:%M %p')} under {name}. "
                            f"I've added it to the calendar. Here is the confirmation link: {booking_link}."
                        )
                        ctx["awaiting_confirmation"] = False
            else:
                # Slot taken; suggest next
                next_slot = find_next_available(requested_dt)
                if next_slot:
                    reply_text = (
                        f"Sorry, {requested_dt.strftime('%A %I:%M %p')} is taken. "
                        f"How about {next_slot.strftime('%A %I:%M %p')} instead?"
                    )
                    ctx["requested_datetime"] = next_slot.isoformat()
                else:
                    reply_text = "Sorry, I can't find a nearby available slot. Could you try another time?"

    # Synthesize speech
    mp3_url = synthesize_elevenlabs(reply_text or "Sorry, I didn't understand that.")
    print("Responding with:", reply_text, "audio:", mp3_url)
    sys.stdout.flush()

    # Loop back for unlimited conversation
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{mp3_url}</Play>
    <Redirect method="POST">/voice?ai_reply_url={mp3_url}</Redirect>
</Response>
"""
    return Response(twiml, mimetype="text/xml")

# === Optional test route for calendar access ===
@app.route("/test_calendar", methods=["GET"])
def test_calendar():
    try:
        service = get_calendar_service()
        events = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            maxResults=3,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        items = events.get("items", [])
        summaries = [e.get("summary", "") for e in items]
        return {"status": "success", "sample": summaries}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# === ENTRY POINT ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
