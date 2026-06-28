import os
from typing import Set
from dotenv import load_dotenv
load_dotenv()

# ─── Gemini ──────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY is not set. Please set it in your .env file or environment."
    )
GEMINI_MODEL = "gemini-2.5-flash-lite"

# ─── Supported languages ──────────────────────────────────────────────
SUPPORTED_LANGUAGES = ["en", "ar", "fr"]

# ─── Qualifying question stages (order matters) ──────────────────────
STAGES = ["greeting", "need", "budget", "timeline", "contact", "scoring", "booking", "closed"]

# ─── Lead scoring thresholds ──────────────────────────────────────────
HOT_BUDGET_THRESHOLD = 1000      # was MINIMUIM_BUDGET
HOT_TIMELINE_WEEKS = 4           # was MINIMUIM_TIMELINE_WEEKS

# ─── Critical fields (kept exactly as you wrote) ─────────────────────
CRITICAL_FIELDS = ["language", "need", "budget", "timeline", "contact"]

# ─── Score labels (fixed the leading space, made lowercase for code) ──
SCORE_LABELS = ["hot", "warm", "cold"]   # was SCORE = ["Hot"," Warm","Cold"]

# ─── Booking config (upgraded from your bool) ────────────────────────
BOOKING_OFFERED_FOR: Set[str] = {"hot", "warm"}  # which scores get a booking option
BOOKING_PHRASE = {
    "hot": "let's grab time now",                      # pushed
    "warm": "if you'd like, we can set up a quick call",  # offered
}
BOOKING_SLOT_COUNT = 3

# ─── History cap ──────────────────────────────────────────────────────
# WHAT: Max number of past turns sent to Gemini on each call.
# WHY: history is appended to forever and re-sent in full every turn.
#      A lead who chats for a while (or rambles) would otherwise mean
#      growing latency and token cost on every single message, including
#      the structured extraction call which runs on the FULL history
#      every turn regardless of stage. A qualification flow only needs
#      8 critical facts — it doesn't need the whole transcript once
#      that conversation runs long. 16 turns (~8 exchanges) comfortably
#      covers a normal greeting → contact flow with room for tangents.
MAX_HISTORY_TURNS = 16

# ─── Slack & Notion ──────────────────────────────────────────────────
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")