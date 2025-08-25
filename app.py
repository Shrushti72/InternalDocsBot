# app.py
import os
import threading
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

# Project imports
from config.settings import (
    SECRET_KEY,
    DEBUG,
    GOOGLE_API_KEY,
    SLACK_BOT_TOKEN,
    SLACK_SIGNING_SECRET,
    # SLACK_APP_TOKEN is optional; if present we'll start Slack in this process
)
from bot.chat_handler import ChatHandler

# Optional Slack imports guarded to avoid errors if Slack isn't used
SLACK_AVAILABLE = True
try:
    from slack_bolt import App as SlackApp
    from slack_bolt.adapter.socket_mode import SocketModeHandler
except Exception:
    SLACK_AVAILABLE = False

# -------------------------------------------------------------------
# Environment
# -------------------------------------------------------------------
load_dotenv()

# Flask App (Web UI)
app = Flask(__name__, template_folder="templates")
app.secret_key = SECRET_KEY

# Single ChatHandler instance (loads vector DB and model once)
chat_handler = ChatHandler()


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    """Render the browser chat UI."""
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    """
    Web chat endpoint.
    Expects JSON: { "message": "your question" }
    Returns: { "reply": "answer text" }
    """
    data = request.get_json(silent=True) or {}
    user_query = (data.get("message") or "").strip()

    if not user_query:
        return jsonify({"reply": "❓ Please enter a question."}), 200

    try:
        answer = chat_handler.answer_query(user_query)
        if not answer:
            answer = "🤔 I couldn't find an answer in the docs."
    except Exception as e:
        print("⚠️ Error in chat handler:", e)
        answer = "⚠️ Sorry, something went wrong while searching the docs."

    return jsonify({"reply": answer}), 200


@app.route("/health", methods=["GET"])
def health():
    """Simple health check."""
    return jsonify(
        {
            "status": "ok",
            "service": "internal-docs-agent",
            "llm": "Gemini",
            "slack_enabled": bool(os.getenv("SLACK_BOT_TOKEN")),
        }
    ), 200


# -------------------------------------------------------------------
# Slack (optional, via Socket Mode)
# -------------------------------------------------------------------
def start_slack_in_thread():
    """
    Starts the Slack bot in a background thread if all tokens are present.
    Uses Socket Mode so you don't need to expose a public HTTP endpoint.
    """
    if not SLACK_AVAILABLE:
        print("💤 Slack SDK not installed. Skipping Slack bot.")
        return

    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
    slack_app_token = os.getenv("SLACK_APP_TOKEN")  # xapp- token (App-Level)

    if not (slack_bot_token and slack_signing_secret and slack_app_token):
        print("💤 Slack tokens missing. Web UI will run, Slack bot is disabled.")
        return

    slack_app = SlackApp(token=slack_bot_token, signing_secret=slack_signing_secret)

    @slack_app.command("/askdocs")
    def askdocs_handler(ack, respond, command):
        ack()
        text = (command.get("text") or "").strip()
        if not text:
            respond("❓ Please provide a question. Example: `/askdocs What is our refund policy?`")
            return
        respond("⏳ Searching docs...")
        try:
            answer = chat_handler.answer_query(text)
        except Exception as e:
            print("⚠️ Slack handler error:", e)
            answer = "⚠️ Sorry, something went wrong while searching the docs."
        respond(f"*Q:* {text}\n*Answer:* {answer}")

    @slack_app.event("app_mention")
    def mention_handler(event, say):
        raw_text = event.get("text", "")
        query = raw_text.strip()
        if not query:
            say("👋 Hi! Ask me something about internal docs.")
            return
        say("🔍 Checking docs...")
        try:
            answer = chat_handler.answer_query(query)
        except Exception as e:
            print("⚠️ Slack mention error:", e)
            answer = "⚠️ Sorry, something went wrong while searching the docs."
        say(f"*Answer:* {answer}")

    def run_socket_mode():
        print("🤖 Starting Slack bot (Socket Mode)... Use `/askdocs` or @mention in Slack.")
        SocketModeHandler(slack_app, slack_app_token).start()

    th = threading.Thread(target=run_socket_mode, daemon=True)
    th.start()


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
if __name__ == "__main__":
    # Optionally start Slack
    start_slack_in_thread()

    port = int(os.getenv("PORT", "5000"))
    print(f"🌐 Web UI running on http://127.0.0.1:{port}  (DEBUG={DEBUG})")
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
