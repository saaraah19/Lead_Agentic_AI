# ─── IMPORTS ──────────────────────────────────────────────────────────
# WHAT: httpx is an HTTP client for making API calls.
# WHY: We need to send POST requests to Slack and Notion.
#      We use AsyncClient because it's fast and non‑blocking (like in Project 2).
import httpx

# WHAT: Logging so we can record success/failure without crashing the app.
import logging

# WHAT: Import the API keys and URLs from config.
# WHY: Centralised config means we can change them without touching this file.
from config import SLACK_WEBHOOK_URL, NOTION_API_KEY, NOTION_DATABASE_ID

# ─── SET UP LOGGING ──────────────────────────────────────────────────
# WHAT: Create a logger with the name of this file.
# WHY: So logs from notifier.py are clearly labelled in the console.
logger = logging.getLogger(__name__)

# ─── HELPER: ASYNC HTTP REQUEST (reusable pattern) ──────────────────
async def _post_to_webhook(url: str, payload: dict) -> bool:
    """
    WHAT: Send a POST request to a webhook URL (Slack or custom).
    WHY: Reusable helper — we use it for Slack (and any other webhook).
    RETURNS: True if successful, False if failed (so caller can log accordingly).

    HOW IT WORKS:
        1. Create an async HTTP client.
        2. Send a POST request with the JSON payload.
        3. Check the status code (2xx = success).
        4. If it fails, log the error and return False.
        5. Always close the client (even on error).
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            if response.status_code in (200, 201, 202):
                logger.info(f"✅ Webhook POST successful: {url}")
                return True
            else:
                # Same reasoning as the Notion log below: don't truncate
                # error bodies, the useful part is often past character 100.
                logger.warning(f"⚠️ Webhook returned {response.status_code}: {response.text}")
                return False
    except Exception as e:
        logger.error(f"❌ Webhook POST failed: {e}")
        return False

# ─── SLACK: SEND A HOT LEAD ALERT ──────────────────────────────────
async def send_slack_alert(lead_profile, session_id: str) -> bool:
    """
    WHAT: Send a message to Slack about a hot lead.
    WHY: The business owner needs to know IMMEDIATELY that a high‑value lead is waiting.
    TRIGGER: Called ONLY for "hot" leads (from agent.py).

    THE SLACK MESSAGE:
        - Emoji: 🔥 for attention.
        - Clear summary: what they need, budget, timeline, contact.
        - Actionable: includes the session_id so the owner can find the full conversation.

    RETURNS: True if sent successfully, False if failed (app continues anyway).

    NOTE: If SLACK_WEBHOOK_URL is empty, we skip and return False (no error).
    """
    # ─── Safety check: if no webhook URL, skip ──────────────────────
    if not SLACK_WEBHOOK_URL:
        logger.warning("⚠️ SLACK_WEBHOOK_URL is not set. Skipping Slack alert.")
        return False

    # ─── Build the message payload ──────────────────────────────────
    # WHAT: Slack expects a JSON payload with 'text' or 'blocks' fields.
    # WHY: Blocks give us a nicer formatted message with sections.
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🔥 HOT LEAD ALERT! 🔥",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Need:*\n{lead_profile.need or 'Not specified'}"},
                    {"type": "mrkdwn", "text": f"*Budget:*\n{lead_profile.budget or 'Not specified'}"},
                    {"type": "mrkdwn", "text": f"*Timeline:*\n{lead_profile.timeline or 'Not specified'}"},
                    {"type": "mrkdwn", "text": f"*Contact:*\n{lead_profile.contact or 'Not specified'}"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Session ID:* `{session_id}`"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "💡 This lead is ready to buy. Reach out within 1 hour for best conversion!"}
                ]
            }
        ]
    }

    # ─── Send the request ──────────────────────────────────────────────
    logger.info(f"📨 Sending Slack alert for session {session_id}")
    success = await _post_to_webhook(SLACK_WEBHOOK_URL, payload)
    return success

# ─── NOTION: LOG A LEAD (every lead, not just hot) ──────────────────
async def log_to_notion(lead_profile, session_id: str) -> bool:
    """
    WHAT: Create a new page in Notion for this lead.
    WHY: Notion acts as the long‑term CRM — the owner can review all leads in one place.
    TRIGGER: Called for EVERY lead (hot, warm, and cold) after scoring.

    HOW IT WORKS:
        1. Build a Notion page with properties (fields).
        2. Send a POST request to the Notion API's /pages endpoint.
        3. The page is created in the database specified by NOTION_DATABASE_ID.

    RETURNS: True if created successfully, False if failed (app continues anyway).

    NOTE: If NOTION_API_KEY or NOTION_DATABASE_ID is empty, we skip.
    """
    # ─── Safety check: if Notion is not configured, skip ─────────────
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logger.warning("⚠️ Notion API key or DB ID is not set. Skipping Notion log.")
        return False

    # ─── Build the Notion page payload ──────────────────────────────
    # WHAT: Notion expects a JSON payload with 'parent' (database ID)
    #       and 'properties' (the fields to fill in the database).
    # WHY: Each property must match the column names in your Notion database.
    #      We use 'rich_text' for text fields and 'select' for single‑choice fields.
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            # ─── Title (required field in Notion) ──────────────────────
            "Name": {
                "title": [
                    {
                        "text": {
                            "content": f"{lead_profile.need or 'New Lead'} - {session_id[:8]}"
                        }
                    }
                ]
            },
            # ─── Custom fields ──────────────────────────────────────────
            "Session ID": {
                "rich_text": [{"text": {"content": session_id}}]
            },
            "Language": {
                "select": {"name": lead_profile.language.upper() if lead_profile.language else "Unknown"}
            },
            "Need": {
                "rich_text": [{"text": {"content": lead_profile.need or ""}}]
            },
            "Budget": {
                "rich_text": [{"text": {"content": lead_profile.budget or ""}}]
            },
            "Timeline": {
                "rich_text": [{"text": {"content": lead_profile.timeline or ""}}]
            },
            "Contact": {
                "rich_text": [{"text": {"content": lead_profile.contact or ""}}]
            },
            "Score": {
                "select": {"name": lead_profile.score.upper() if lead_profile.score else "UNKNOWN"}
            }
        }
    }

    # ─── Set up the headers (authentication) ──────────────────────────
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",   # Notion API version (stable)
        "Content-Type": "application/json",
    }

    # ─── Send the request ──────────────────────────────────────────────
    NOTION_API_URL = "https://api.notion.com/v1/pages"
    logger.info(f"📝 Logging lead to Notion for session {session_id}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(NOTION_API_URL, json=payload, headers=headers)
            if response.status_code in (200, 201, 202):
                logger.info(f"✅ Notion page created for session {session_id}")
                return True
            else:
                # WHAT: Log the FULL response body, not response.text[:100].
                # WHY: Notion's validation errors put the actually useful part
                #      (which property is wrong, or what it expected) AFTER
                #      the first 100 characters more often than not — e.g.
                #      "Session ID is not a property that exists." is itself
                #      44 chars, but once it's prefixed with the JSON
                #      envelope ({"object":"error","status":400,"code":...)
                #      the real message gets sliced off mid-sentence. A
                #      config/schema mismatch should be immediately legible
                #      in the logs, not something you have to reproduce and
                #      screenshot to read in full.
                logger.warning(f"⚠️ Notion API returned {response.status_code}: {response.text}")
                return False
    except Exception as e:
        logger.error(f"❌ Notion API call failed: {e}")
        return False