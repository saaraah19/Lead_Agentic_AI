# booking.py
import re
import sqlite3
from datetime import datetime
from calendar_utils import generate_available_slots, create_calendar_event, _get_busy_times, _slot_is_busy
import db  # leads.db — used for the UNIQUE race-condition guard on bookings.slot_time


# ============================================
# 1. FONCTION PUBLIQUE POUR RÉCUPÉRER LES CRÉNEAUX
# ============================================
def get_available_slots(slot_count=6):
    """
    Interface publique pour récupérer les créneaux.
    Appelle directement l'API Google Calendar.
    """
    return generate_available_slots(slot_count=slot_count)


# ============================================
# 1.b FORMATAGE DES CRÉNEAUX POUR LE PROMPT
# ============================================
def format_slots_for_prompt(slots: list) -> str:
    """
    Transforme la liste de créneaux en texte lisible pour le prompt.
    Exemple de sortie :
      1. Mon 10:00 AM
      2. Mon 02:00 PM
      3. Tue 10:00 AM
    """
    if not slots:
        return "Aucun créneau disponible pour le moment."
    return "\n".join(f"{s['index']}. {s['display']}" for s in slots)


# ============================================
# 1.c PARSING DU CHOIX UTILISATEUR
# ============================================
def parse_booking_choice(text: str, slots: list) -> str | None:
    """
    Parse free-text user input into a slot ISO string.
    Returns the matching slot's ISO string, or None if unparseable.

    Handles in priority order:
      1. Ordinal words  — "the first one", "the last slot"
      2. Time of day    — "2pm", "10am", "14h"
      3. Slot number    — "2", "slot 3", "I'll take the 3rd"
      4. Day name       — "wednesday", "mercredi", "tue"
    """
    if not slots or not text:
        return None

    text_lower = text.lower().strip()

    # 1. Ordinal keywords (checked before digits to avoid "1st" → index 1 accidentally)
    if any(w in text_lower for w in ["first", "premier", "1st", "الأول"]):
        return slots[0]["slot"]
    if any(w in text_lower for w in ["last", "dernier", "الأخير"]):
        return slots[-1]["slot"]

    # 2. Time of day: "2pm", "10 am", "14h"
    time_match = re.search(r'\b(\d{1,2})\s*(am|pm|h)\b', text_lower)
    if time_match:
        hour = int(time_match.group(1))
        meridiem = time_match.group(2)
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        for slot in slots:
            if datetime.fromisoformat(slot["slot"]).hour == hour:
                return slot["slot"]

    # 3. Slot index number
    digit_match = re.search(r'\b(\d+)\b', text_lower)
    if digit_match:
        idx = int(digit_match.group(1))
        for slot in slots:
            if slot["index"] == idx:
                return slot["slot"]

    # 4. Day names (English + French)
    day_map = {
        "monday": 0, "mon": 0, "lundi": 0,
        "tuesday": 1, "tue": 1, "mardi": 1,
        "wednesday": 2, "wed": 2, "mercredi": 2,
        "thursday": 3, "thu": 3, "jeudi": 3,
        "friday": 4, "fri": 4, "vendredi": 4,
    }
    for word, weekday in day_map.items():
        if word in text_lower:
            for slot in slots:
                if datetime.fromisoformat(slot["slot"]).weekday() == weekday:
                    return slot["slot"]

    return None


# ============================================
# 2. CONFIRMATION DE RÉSERVATION
# ============================================
def confirm_booking(
    slot_index: int,
    lead_email: str,
    lead_name: str = "",
    lead_need: str = "",
    language: str = "fr",
    session_id: str = "",
):
    """
    Confirme la réservation :
    1. Trouve le créneau sélectionné
    2. Vérifie une dernière fois que le créneau est libre (freebusy)
    3. Réclame le créneau dans la DB AVANT l'appel calendrier
       (garde atomique contre les doubles réservations concurrentes)
    4. Crée l'événement dans Google Calendar
       Si ça échoue : annule la réservation DB pour que le créneau soit retentable
    5. Sauvegarde dans SQLite local pour audit
    6. Retourne le message de confirmation localisé

    POURQUOI DB AVANT CALENDRIER :
        Deux requêtes simultanées peuvent toutes les deux passer le check
        freebusy si elles arrivent dans le même intervalle. La contrainte
        UNIQUE sur bookings.slot_time garantit qu'une seule réussira l'INSERT.
        La deuxième reçoit un message "créneau pris" avant même de toucher
        l'API Google Calendar.
    """
    # ─── Récupérer les créneaux actuels ─────────────────────────────
    available = generate_available_slots()
    selected = next((s for s in available if s["index"] == slot_index), None)

    if not selected:
        return {
            "status": "error",
            "message": get_message("slot_unavailable", language)
        }

    # ─── Vérification freebusy (Google Calendar) ────────────────────
    slot_dt = datetime.fromisoformat(selected["slot"])
    busy_times = _get_busy_times()

    if _slot_is_busy(slot_dt, busy_times):
        return {
            "status": "error",
            "message": get_message("slot_taken", language)
        }

    # ─── Garde DB atomique (AVANT l'appel calendrier) ───────────────
    # WHAT: INSERT avec contrainte UNIQUE sur slot_time.
    # WHY: Si deux requêtes passent le check freebusy simultanément,
    #      une seule réussira cet INSERT. L'autre reçoit False et
    #      retourne une erreur propre sans créer d'événement dupliqué.
    slot_claimed = db.save_booking(session_id or lead_email, selected["slot"])
    if not slot_claimed:
        return {
            "status": "error",
            "message": get_message("slot_taken", language)
        }

    # ─── Créer l'événement Google Calendar ──────────────────────────
    try:
        event_url = create_calendar_event(
            slot_iso=selected["slot"],
            lead_email=lead_email,
            lead_name=lead_name,
            lead_need=lead_need
        )
    except Exception as e:
        # Annuler la réservation DB pour que le créneau soit retentable.
        # Si le rollback échoue lui aussi, le créneau reste bloqué en DB
        # mais le check freebusy (Google) laissera passer les prochaines
        # tentatives — Google Calendar est la source de vérité pour la dispo.
        print(f"❌ Erreur lors de la création de l'événement : {e}")
        try:
            with db.get_db() as conn:
                conn.execute(
                    "DELETE FROM bookings WHERE slot_time = ?",
                    (selected["slot"],)
                )
                conn.commit()
        except Exception as rollback_err:
            print(f"⚠️ Rollback DB échoué : {rollback_err}")
        return {
            "status": "error",
            "message": get_message("calendar_error", language)
        }

    # ─── Sauvegarde audit local (non critique) ──────────────────────
    try:
        save_booking_audit(selected["slot"], lead_email, lead_name, lead_need)
    except Exception as e:
        print(f"⚠️ Erreur d'écriture audit (non critique) : {e}")

    # ─── Message de confirmation localisé ───────────────────────────
    display_time = selected["display"]
    message = get_message("booking_confirmed", language).format(time=display_time)

    return {
        "status": "success",
        "message": message,
        "event_url": event_url,
        "slot": selected["slot"]
    }


# ============================================
# 3. MESSAGES MULTILINGUES
# ============================================
def get_message(key: str, language: str = "fr") -> str:
    messages = {
        "booking_confirmed": {
            "en": "✅ You're booked for {time}! Check your email for the calendar invite.",
            "fr": "✅ Vous êtes réservé pour {time} ! Consultez votre email pour l'invitation.",
            "ar": "✅ تم حجزك في {time}! تحقق من بريدك الإلكتروني للحصول على الدعوة."
        },
        "slot_unavailable": {
            "en": "❌ This slot is no longer available. Please choose another one.",
            "fr": "❌ Ce créneau n'est plus disponible. Veuillez en choisir un autre.",
            "ar": "❌ هذا الموعد غير متاح. الرجاء اختيار موعد آخر."
        },
        "slot_taken": {
            "en": "❌ Sorry, this slot was just taken. Please choose another.",
            "fr": "❌ Désolé, ce créneau vient d'être pris. Veuillez en choisir un autre.",
            "ar": "❌ عذراً، هذا الموعد محجوز حالياً. الرجاء اختيار موعد آخر."
        },
        "calendar_error": {
            "en": "❌ A technical error occurred. Please try again later.",
            "fr": "❌ Une erreur technique est survenue. Veuillez réessayer plus tard.",
            "ar": "❌ حدث خطأ تقني. الرجاء المحاولة لاحقاً."
        }
    }
    return messages.get(key, {}).get(language, messages[key]["fr"])


# ============================================
# 4. SAUVEGARDE SQLITE LOCALE (AUDIT LOG)
# ============================================
def save_booking_audit(slot_iso: str, email: str, name: str, need: str):
    """
    Conserve une trace locale de la réservation dans bookings.db.
    Séparé de db.save_booking() (leads.db) qui sert de garde de course.
    """
    conn = sqlite3.connect("bookings.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot TEXT UNIQUE,
            email TEXT,
            name TEXT,
            need TEXT,
            booked_at TEXT
        )
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO bookings_audit (slot, email, name, need, booked_at)
        VALUES (?, ?, ?, ?, ?)
    """, (slot_iso, email, name, need, datetime.now().isoformat()))
    conn.commit()
    conn.close()
