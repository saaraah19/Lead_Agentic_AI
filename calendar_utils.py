# calendar_utils.py
import os
import json
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# === Chargement des variables d'environnement ===
load_dotenv()  # Lit le fichier .env

# === Configuration ===
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
BUSINESS_TZ = pytz.timezone("Africa/Algiers")  # Utilisation de pytz

def _get_calendar_service():
    """Construit et retourne le service Google Calendar."""
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("❌ GOOGLE_CREDENTIALS_JSON non défini dans .env")
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)

def _get_busy_times(days_ahead=7):
    """Récupère les créneaux occupés depuis Google Calendar."""
    service = _get_calendar_service()
    now = datetime.now(BUSINESS_TZ)
    end = now + timedelta(days=days_ahead)

    body = {
        "timeMin": now.astimezone().isoformat(),
        "timeMax": end.astimezone().isoformat(),
        "items": [{"id": CALENDAR_ID}],
    }
    result = service.freebusy().query(body=body).execute()
    busy = result["calendars"][CALENDAR_ID]["busy"]
    return {(b["start"], b["end"]) for b in busy}

def _slot_is_busy(slot_dt: datetime, busy_times: set) -> bool:
    """Vérifie si un créneau de 15 minutes est occupé."""
    slot_end = slot_dt + timedelta(minutes=15)
    for start, end in busy_times:
        busy_start = datetime.fromisoformat(start)
        busy_end = datetime.fromisoformat(end)
        if slot_dt < busy_end and slot_end > busy_start:
            return True
    return False

def generate_available_slots(slot_count=6, days_ahead=7):
    """Génère les créneaux disponibles (jours ouvrés, 10h et 14h)."""
    busy_times = _get_busy_times(days_ahead)
    available = []
    index = 1

    current = datetime.now(BUSINESS_TZ).date() + timedelta(days=1)
    max_attempts = days_ahead * 5
    attempts = 0

    while len(available) < slot_count and attempts < max_attempts:
        attempts += 1
        if current.weekday() in [5, 6]:  # samedi, dimanche
            current += timedelta(days=1)
            continue

        for hour in [10, 14]:
            slot_dt = datetime(
                current.year, current.month, current.day,
                hour, 0, tzinfo=BUSINESS_TZ
            )
            if not _slot_is_busy(slot_dt, busy_times):
                available.append({
                    "slot": slot_dt.isoformat(),
                    "display": slot_dt.strftime("%a %I:%M %p"),
                    "index": index,
                })
                index += 1
                if len(available) >= slot_count:
                    break
        current += timedelta(days=1)

    return available


# calendar_utils.py - fonction create_calendar_event (version corrigée)

# calendar_utils.py - fonction create_calendar_event (version corrigée avec conversion UTC)

def create_calendar_event(slot_iso: str, lead_email: str, lead_name: str = "", lead_need: str = ""):
    """Crée un événement dans Google Calendar (sans lien Meet, sans invité)."""
    service = _get_calendar_service()
    start = datetime.fromisoformat(slot_iso)
    end = start + timedelta(minutes=15)

    # ✅ Conversion en UTC pour éviter les problèmes de fuseau horaire
    start_utc = start.astimezone(pytz.UTC)
    end_utc = end.astimezone(pytz.UTC)

    summary = f"Discovery call - {lead_name or 'New lead'}"
    if lead_need:
        summary += f" ({lead_need[:30]})"

    event = {
        "summary": summary,
        "description": f"15-minute intro call booked via chatbot.\nLead: {lead_email}\nNeed: {lead_need or 'Not specified'}",
        "start": {
            "dateTime": start_utc.isoformat(),  # Envoyer en UTC
            "timeZone": "Africa/Algiers"        # Mais afficher dans ce fuseau
        },
        "end": {
            "dateTime": end_utc.isoformat(),
            "timeZone": "Africa/Algiers"
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 15},
            ],
        },
    }

    created = service.events().insert(
        calendarId=CALENDAR_ID,
        body=event,
        sendUpdates="none",
    ).execute()

    return created.get("htmlLink", "")