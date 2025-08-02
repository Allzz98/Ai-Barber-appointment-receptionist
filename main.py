import os
import sys
import json
import time
import datetime
from flask import Flask, request, Response
import requests
from dateutil import parser as dateparser
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Flush prints immediately for Render logs
sys.stdout.reconfigure(line_buffering=True)

app = Flask(__name__)

# === CONFIG / ENV ===
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.environ.get("TWILIO_AUTH_TOKEN")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")
BASE_URL = os.environ.get("BASE_URL")  # e.g., https://your-app.onrender.com

# Service account JSON file handling (writes from env if not present)
SERVICE_ACCOUNT_FILENAME = "barber-shop-ai-booking-system-1daece25cca2.json"
if not os.path.exists(SERVICE_ACCOUNT_FILENAME):
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        with open(SERVICE_ACCOUNT_FILENAME, "w") as f:
            f.write(sa_json)

# Timezone for calendar events
TIMEZONE = "Australia/Brisbane"

# Greeting audio file path (should exist under static/)
GREETING_MP3_PATH = "/static/test.mp3"

# Simple in-memory per-call context (lost on restart)
contexts: dict[str, dict] = {}

# === HELPERS ===

def get_base_url():
    if BASE_URL:
        return BASE_URL.rstrip("/")
    return request.url_root.rstrip("/")

def get_calendar_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILENAME,
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return service

def is_slot_available(requested_dt: datetime.datetime, duration_minutes=60):
    try:
        service = get_calendar_service()
        start = requested_dt.isoformat()
        end = (requested_dt + datetime.timedelta(minutes=duration_minutes)).isoformat()
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
        return False

def find_next_available(start_dt: datetime.datetime, duration_minutes=60, max_tries=8):
    candidate = start_dt
    for _ in range(max_tries):
        if is_slot_available(candidate, duration_minutes):
            return candidate
        candidate += datetime.timedelta(minutes=30)
    return None

def create_booking(name: str, service_desc: str, start_dt: datetime.datetime, duration_minutes=60):
    try:
        service = get_calendar_service()
        event = {
            "summary": f"{service_desc} - {name}",
            "description": f"{service_desc} booked by {name}",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": (start_dt + datetime.timedelta(minutes=duration_minutes)).isoformat(), "timeZone": TIMEZONE},
        }
        created = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        return created.get("htmlLink")
    except Exception as e:
        print("Error creating booking:", e)
        return None

def transcribe_audio(audio_bytes):
    if not OPENAI_API_KEY:
        print("Missing OpenAI API key.")
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
        resp_json = resp.json()
        if resp.ok and "text" in resp_json:
            return resp_json["text"]
        else:
            print("Whisper transcription error:", resp_json)
            return ""
    except Exception as e:
        print("Exception during transcription:", e)
        return ""

def chatgpt_parse_and_respond(call_sid: str, user_message: str):
    if not OPENAI_API_KEY:
        return "Sorry, AI not configured.", {}
    # maintain context
    ctx = contexts.setdefault(call_sid, {
        "name": None,
        "requested_datetime": None,
        "service": None,
        "booking_confirmed": False,
        "awaiting_confirmation": False,
        "last_reply": None
    })

    system_prompt = (
        "You are a professional barbershop receptionist. "
        "Help the caller book appointments by gathering name, service, and desired date/time. "
        "If they request a booking, ask for missing pieces one at a time. "
        "Once you have name, service, and datetime, wait for explicit confirmation before booking. "
        "If a slot is unavailable, you will be told by the backend and should suggest alternatives. "
        "Provide output as a JSON object with keys: "
        "\"reply_text\", \"name\" (if given), \"service\" (if given), "
        "\"requested_datetime\" (ISO string if given), \"booking_intent\" (bool), "
        "\"need_confirmation\" (bool), \"booking_confirmed\" (bool), "
        "\"ask_for\" (one of 'name','service','datetime','confirmation', or null)."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    # expose existing context so model can incorporate
    if ctx["name"]:
        messages.append({"role": "user", "content": f"Name on file: {ctx['name']}"})
    if ctx["service"]:
        messages.append({"role": "user", "content": f"Service on file: {ctx['service']}"})
    if ctx["requested_datetime"]:
        messages.append({"role": "user", "content": f"Requested datetime on file: {ctx['requested_datetime']}"})

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 400
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        print("ChatGPT raw response:", response.text)
        sys.stdout.flush()
        data = response.json()
        if "choices" in data and len(data["choices"]) > 0:
            content = data["choices"][0]["message"]["content"].strip()
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                # fallback simple structure
                parsed = {
                    "reply_text": content,
                    "name": ctx["name"],
                    "service": ctx["service"],
                    "requested_datetime": ctx["requested_datetime"],
                    "booking_intent": False,
                    "need_confirmation": False,
                    "booking_confirmed": False,
                    "ask_for": None
                }
            # update context
            if parsed.get("name"):
                ctx["name"] = parsed["name"]
            if parsed.get("service"):
                ctx["service"] = parsed["service"]
            if parsed.get("requested_datetime"):
                # normalize
                try:
                    dt = dateparser.parse(parsed["requested_datetime"])
                    ctx["requested_datetime"] = dt.isoformat()
                except Exception:
                    pass
            if parsed.get("booking_confirmed"):
                ctx["booking_confirmed"] = True
            if parsed.get("need_confirmation"):
                ctx["awaiting_confirmation"] = True
            ctx["last_reply"] = parsed.get("reply_text")
            return parsed.get("reply_text", ""), parsed
        else:
            print("Unexpected ChatGPT structure:", data)
            return "Sorry, I had trouble responding.", {}
    except Exception as e:
        print("ChatGPT exception:", e)
        return "Sorry, the AI service is unavailable right now.", {}

def synthesize_elevenlabs(text: str) -> str:
    if not ELEVENLABS_API_KEY:
        print("Missing ElevenLabs API key.")
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
            return f"{get_base_url()}/static/ai_reply.mp3"
        else:
            print("ElevenLabs error:", response.text)
            return f"{get_base_url()}{GREETING_MP3_PATH}"
    except Exception as e:
        print("Exception in ElevenLabs:", e)
        return f"{get_base_url()}{GREETING_MP3_PATH}"

def fetch_recording_bytes(recording_url):
    candidates = [recording_url + ".wav", recording_url + ".mp3", recording_url]
    for attempt in range(3):
        for url_try in candidates:
            try:
                print(f"[fetch_recording_bytes] Attempt {attempt+1} checking {url_try}")
                sys.stdout.flush()
                resp = requests.get(url_try, auth=(TWILIO_SID, TWILIO_AUTH), timeout=10)
                if resp.ok:
                    print(f"[fetch_recording_bytes] Success from {url_try}")
                    sys.stdout.flush()
                    return resp.content
                else:
                    print(f"[fetch_recording_bytes] Failed {url_try}: {resp.status_code}")
                    sys.stdout.flush()
            except Exception as e:
                print(f"[fetch_recording_bytes] Exception for {url_try}: {e}")
                sys.stdout.flush()
        time.sleep(1)
    return None

def twiml_error(message: str):
    print("Sending error to caller:", message)
    sys.stdout.flush()
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
    print("Health check / home")
    sys.stdout.flush()
    return "AI Barbershop receptionist is live."

@app.route("/voice", methods=["POST"])
def voice():
    ai_reply_url = request.values.get("ai_reply_url")
    print("Voice endpoint hit; ai_reply_url:", ai_reply_url)
    sys.stdout.flush()

    if ai_reply_url:
        # play last AI reply then record again
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{ai_reply_url}</Play>
    <Record maxLength="15" action="/process_recording" playBeep="true" timeout="5" />
</Response>
"""
    else:
        greeting_url = f"{get_base_url()}{GREETING_MP3_PATH}"
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{greeting_url}</Play>
    <Say>Welcome to Fresh Fade Barbershop. How can I help you today?</Say>
    <Record maxLength="15" action="/process_recording" playBeep="true" timeout="5" />
</Response>
"""
    return Response(twiml, mimetype="text/xml")

@app.route("/process_recording", methods=["POST"])
def process_recording():
    print("process_recording invoked; form:", request.form)
    sys.stdout.flush()

    call_sid = request.form.get("CallSid", "")
    recording_url = request.form.get("RecordingUrl")
    if not recording_url:
        return twiml_error("I didn't get your voice. Please try again.")

    # fetch audio (with retries / extension fallbacks)
    audio_bytes = fetch_recording_bytes(recording_url)
    if not audio_bytes:
        return twiml_error("Sorry, I couldn't retrieve your message.")

    # transcription
    transcript = transcribe_audio(audio_bytes)
    print("Transcript:", transcript)
    sys.stdout.flush()

    # ChatGPT parsing & decision
    reply_text, parsed = chatgpt_parse_and_respond(call_sid, transcript)

    # booking logic
    ctx = contexts.get(call_sid, {})
    booking_link = None
    if parsed.get("booking_intent"):
        # parse requested datetime from context
        requested_iso = ctx.get("requested_datetime")
        requested_dt = None
        if requested_iso:
            try:
                requested_dt = dateparser.parse(requested_iso)
            except Exception:
                requested_dt = None

        if requested_dt and not ctx.get("booking_confirmed"):
            available = is_slot_available(requested_dt)
            if available:
                if not ctx.get("awaiting_confirmation"):
                    reply_text = f"Great, I can book you for {requested_dt.strftime('%A %I:%M %p')}. Should I confirm this appointment?"
                    ctx["awaiting_confirmation"] = True
                else:
                    if ctx.get("booking_confirmed"):
                        name = ctx.get("name") or "Client"
                        service = ctx.get("service") or "Haircut"
                        booking_link = create_booking(name, service, requested_dt)
                        if booking_link:
                            reply_text = (
                                f"Booking confirmed for {requested_dt.strftime('%A %I:%M %p')} under {name}. "
                                f"I've added it to the calendar."
                            )
                        else:
                            reply_text = "I tried to book it but something went wrong. Please try again."
                        ctx["awaiting_confirmation"] = False
            else:
                next_slot = find_next_available(requested_dt) if requested_dt else None
                if next_slot:
                    reply_text = (
                        f"Sorry, {requested_dt.strftime('%A %I:%M %p')} is taken. "
                        f"How about {next_slot.strftime('%A %I:%M %p')} instead?"
                    )
                    ctx["requested_datetime"] = next_slot.isoformat()
                else:
                    reply_text = "Sorry, I can't find a nearby available slot. Could you try another time?"

    # synthesize reply
    mp3_url = synthesize_elevenlabs(reply_text or "Sorry, I didn't understand that.")
    print("Replying with:", reply_text, "audio:", mp3_url)
    sys.stdout.flush()

    # loop back for next turn
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{mp3_url}</Play>
    <Redirect method="POST">/voice?ai_reply_url={mp3_url}</Redirect>
</Response>
"""
    return Response(twiml, mimetype="text/xml")

@app.route("/test_calendar", methods=["GET"])
def test_calendar():
    try:
        service = get_calendar_service()
        events = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            maxResults=5,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        items = events.get("items", [])
        summaries = [e.get("summary", "") for e in items]
        return {"status": "success", "sample": summaries}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# === ENTRY ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
