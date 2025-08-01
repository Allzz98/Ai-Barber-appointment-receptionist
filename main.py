from flask import Flask, Response

app = Flask(__name__)

@app.route("/voice", methods=["POST"])
def voice():
    # TwiML for Twilio - must be Content-Type: text/xml
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>https://ai-barber-appointment-receptionist.onrender.com/static/test.mp3</Play>
</Response>
"""
    return Response(twiml, mimetype="text/xml")

@app.route("/", methods=["GET"])
def home():
    return "AI Barbershop is online."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
