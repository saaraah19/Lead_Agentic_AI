# booking.py
import sqlite3
from datetime import datetime
from calendar_utils import generate_available_slots, create_calendar_event, _get_busy_times, _slot_is_busy

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
# 2. CONFIRMATION DE RÉSERVATION (NOUVELLE VERSION)
# ============================================
def confirm_booking(slot_index: int, lead_email: str, lead_name: str = "", lead_need: str = "", language: str = "fr"):
    """
    Confirme la réservation :
    1. Vérifie une dernière fois que le créneau est libre
    2. Crée l'événement dans Google Calendar
    3. Sauvegarde dans SQLite pour audit
    4. Retourne le message de confirmation localisé
    """
    # Récupérer les créneaux actuels (pour avoir le détail du slot sélectionné)
    available = generate_available_slots()
    selected = None
    for slot in available:
        if slot["index"] == slot_index:
            selected = slot
            break

    if not selected:
        return {
            "status": "error",
            "message": get_message("slot_unavailable", language)
        }

    # Vérification de conflit juste avant la création (sécurité)
    slot_dt = datetime.fromisoformat(selected["slot"])
    busy_times = _get_busy_times()

    if _slot_is_busy(slot_dt, busy_times):
        return {
            "status": "error",
            "message": get_message("slot_taken", language)
        }

    # 1. Créer l'événement Google Calendar
    try:
        event_url = create_calendar_event(
            slot_iso=selected["slot"],
            lead_email=lead_email,
            lead_name=lead_name,
            lead_need=lead_need
        )
    except Exception as e:
        print(f"❌ Erreur lors de la création de l'événement : {e}")
        return {
            "status": "error",
            "message": get_message("calendar_error", language)
        }

    # 2. Sauvegarde dans SQLite (audit log)
    try:
        save_booking_audit(selected["slot"], lead_email, lead_name, lead_need)
    except Exception as e:
        print(f"⚠️ Erreur d'écriture SQLite (non critique) : {e}")

    # 3. Message de confirmation localisé
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
# 4. SAUVEGARDE SQLITE (AUDIT LOG)
# ============================================
def save_booking_audit(slot_iso: str, email: str, name: str, need: str):
    """
    Conserve une trace locale de la réservation.
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