# ─── IMPORTS ──────────────────────────────────────────────────────────
import re
import asyncio
import logging
import sqlite3
from typing import List, Optional
from datetime import datetime

from db import get_session, create_session, update_session, save_lead
from generator import call_gemini, extract_lead_profile
from model import LeadProfile
from config import (
    CRITICAL_FIELDS, STAGES, BOOKING_OFFERED_FOR,
    BOOKING_PHRASE, STAGE_TEMPERATURE,
)
from scorer import score_lead
from notifier import send_slack_alert, log_to_notion
from booking import (
    generate_available_slots, format_slots_for_prompt,
    confirm_booking, parse_booking_choice,
)

# ─── Logging ──────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ─── Language detection ──────────────────────────────────────────────
def detect_language(text: str) -> str:
    if re.search(r'[\u0600-\u06FF]', text):
        return "ar"
    lower = text.lower()
    french_markers = ['é', 'è', 'ê', 'à', 'ù', 'ç', 'ô', 'â', 'î',
                       'bonjour', 'merci', 'salut', 'besoin', 'site web']
    if any(m in lower for m in french_markers):
        return "fr"
    return "en"

# ─── FALLBACK EXTRACTION (regex) ─────────────────────────────────────
def _fallback_extract(text: str) -> dict:
    result = {"need": "", "budget": "", "timeline": "", "contact": ""}

    # ─── Budget ──────────────────────────────────────────────────────
    budget_patterns = [
        (r'\$?([\d,]+)\s*(k|K|thousand|grand|grands)', True),
        (r'\$([\d,]+)', False),
        (r'(\d+)\s*(k|K|thousand|grand|grands)', True),
        (r'(\d+)', False),
    ]
    for pattern, has_multiplier in budget_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            num = match.group(1).replace(',', '')
            if has_multiplier and match.group(2) in ('k', 'K', 'thousand', 'grand', 'grands'):
                result["budget"] = f"${int(float(num)) * 1000}"
            else:
                result["budget"] = f"${num}"
            break

    # ─── Timeline ────────────────────────────────────────────────────
    timeline_match = re.search(r'(\d+)\s*(week|month|day|year|weeks|months|days|years)', text, re.IGNORECASE)
    if timeline_match:
        num = timeline_match.group(1)
        unit = timeline_match.group(2)
        if unit.endswith('s'):
            unit = unit[:-1]
        result["timeline"] = f"{num} {unit}{'s' if int(num) > 1 else ''}"
    elif re.search(r'\b(ASAP|immediate|now|today)\b', text, re.IGNORECASE):
        result["timeline"] = "ASAP"

    # ─── Contact ────────────────────────────────────────────────────
    email = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    if email:
        result["contact"] = email.group(0)
    else:
        phone = re.search(r'(\+?[\d\s\-\(\)]{10,15})', text)
        if phone:
            result["contact"] = phone.group(0)

    # ─── Need ──────────────────────────────────────────────────────
    need_keywords = {
        "website": "Website",
        "automation": "Automation",
        "booking": "Booking System",
        "restaurant": "Restaurant Management",
        "app": "Mobile App",
    }
    for keyword, value in need_keywords.items():
        if keyword in text.lower():
            result["need"] = value
            break

    return result


# ─── MAIN ENTRY POINT ──────────────────────────────────────────────────
async def process_message(session_id: str, user_message: str) -> str:
    session = get_session(session_id)
    if not session:
        create_session(session_id)
        session = get_session(session_id)

    # ─── Early exit: closed ──────────────────────────────────────────
    if session.get("stage") == "closed":
        closing = {
            "en": "Thanks for reaching out! We'll be in touch soon. 👋",
            "ar": "شكراً للتواصل معنا! سنتابع معك قريباً. 👋",
            "fr": "Merci pour votre message ! Nous reviendrons vers vous bientôt. 👋",
        }
        lang = session.get("language", "en") or "en"
        return closing.get(lang, closing["en"])

    language = session.get("language", "")
    stage = session.get("stage", "greeting")
    history = session.get("history", [])
    profile_fields = session.get("profile_fields", {})

    if not language:
        language = detect_language(user_message)
        update_session(session_id, language=language)

    history.append({"role": "user", "content": user_message})

    # ─── Gemini extraction (best effort) ──────────────────────────────
    # _ discards the intermediate LeadProfile returned by _update_lead_profile;
    # we rebuild it from the merged profile_fields a few lines below.
    if stage not in ("scoring", "booking", "closed"):
        try:
            _, profile_fields = await _update_lead_profile(
                session_id, history, language, profile_fields
            )
        except Exception as e:
            logger.warning(f"Gemini extraction failed: {e}")

    # ─── Always apply fallback for any missing fields ──────────────────
    fallback = _fallback_extract(user_message)
    for key in ["need", "budget", "timeline", "contact"]:
        if not profile_fields.get(key) and fallback.get(key):
            profile_fields[key] = fallback[key]

    if fallback.get("budget") and not profile_fields.get("budget_usd_estimate"):
        num_match = re.search(r'\$?([\d,]+)', fallback["budget"])
        if num_match:
            num = float(num_match.group(1).replace(',', ''))
            if "k" in fallback["budget"].lower() or "grand" in fallback["budget"].lower():
                num *= 1000
            profile_fields["budget_usd_estimate"] = num

    if fallback.get("timeline") and not profile_fields.get("timeline_weeks_estimate"):
        num_match = re.search(r'(\d+)', fallback["timeline"])
        if num_match:
            profile_fields["timeline_weeks_estimate"] = float(num_match.group(1))

    update_session(session_id, profile_fields=profile_fields)

    lead_profile = LeadProfile(
        language=language,
        need=profile_fields.get("need", ""),
        budget=profile_fields.get("budget", ""),
        timeline=profile_fields.get("timeline", ""),
        contact=profile_fields.get("contact", ""),
        score="unknown",
        stage="greeting",
    )

    # ─── If complete, score immediately ──────────────────────────────
    if stage not in ("scoring", "booking", "closed") and _is_complete(lead_profile):
        score = score_lead(lead_profile, profile_fields)
        lead_profile.score = score

        try:
            save_lead(session_id, lead_profile)
        except sqlite3.IntegrityError:
            logger.warning(f"Lead already saved for session {session_id} — skipping duplicate insert.")

        if score == "hot":
            await send_slack_alert(lead_profile, session_id)
        await log_to_notion(lead_profile, session_id)

        if score in BOOKING_OFFERED_FOR:
            profile_fields["score"] = score
            profile_fields["slots_presented"] = False
            update_session(session_id, stage="booking", profile_fields=profile_fields)
            stage = "booking"
        else:
            update_session(session_id, stage="closed")
            cold_close = {
                "en": "Thanks for your interest! We'll be in touch soon. 👋",
                "ar": "شكراً لتواصلك معنا! سنعود إليك قريباً. 👋",
                "fr": "Merci pour votre intérêt ! Nous reviendrons vers vous bientôt. 👋",
            }
            return cold_close.get(language, cold_close["en"])

    # ─── Advance stage based on what's filled ──────────────────────────
    if stage not in ("scoring", "booking", "closed"):
        next_stage = _get_next_stage(stage, lead_profile)
        if next_stage != stage:
            update_session(session_id, stage=next_stage)
            stage = next_stage

    # ─── Handle booking stage ─────────────────────────────────────────
    if stage == "booking":
        # 1. Récupérer les créneaux proposés (les générer si absents)
        available_slots = profile_fields.get("offered_slots")
        if not available_slots:
            available_slots = generate_available_slots()
            profile_fields["offered_slots"] = available_slots
            profile_fields["slots_presented"] = False
            update_session(session_id, profile_fields=profile_fields)

        # 2. Vérifier que l'utilisateur a bien fourni un email (contact)
        lead_email = profile_fields.get("contact", "")
        if not lead_email:
            system_prompt = _build_system_prompt("contact", language)
            bot_reply = await asyncio.to_thread(
                call_gemini,
                system_prompt=system_prompt,
                user_message=user_message,
                history=history,
                temperature=0.3,
            )
            history.append({"role": "model", "content": bot_reply})
            update_session(session_id, history=history)
            return bot_reply

        # 3. Vérifier si les créneaux ont déjà été présentés
        slots_presented = profile_fields.get("slots_presented", False)

        # 4. Créneaux déjà présentés → parser le choix de l'utilisateur
        if slots_presented:
            # Use the tested parse_booking_choice from booking.py (returns ISO string),
            # then resolve to the index that confirm_booking expects.
            slot_iso = parse_booking_choice(user_message, available_slots)
            slot_index = (
                next((s["index"] for s in available_slots if s["slot"] == slot_iso), None)
                if slot_iso else None
            )

            if slot_index is None:
                fallback_message = {
                    "en": "I didn't understand which slot you want. Please reply with the number (1, 2, 3, ...) or say the day and time (e.g., 'Thursday 10am').",
                    "fr": "Je n'ai pas compris quel créneau vous voulez. Veuillez répondre avec le numéro (1, 2, 3, ...) ou indiquer le jour et l'heure (ex: 'jeudi 10h').",
                    "ar": "لم أفهم أي موعد تريد. الرجاء الرد برقم (1، 2، 3، ...) أو ذكر اليوم والوقت (مثال: 'الخميس 10 صباحاً')."
                }
                bot_reply = fallback_message.get(language, fallback_message["fr"])
                history.append({"role": "model", "content": bot_reply})
                update_session(session_id, history=history)
                return bot_reply

            # Choix valide — confirmer la réservation
            result = confirm_booking(
                slot_index=slot_index,
                lead_email=lead_email,
                lead_name=profile_fields.get("name", "Lead"),
                lead_need=profile_fields.get("need", ""),
                language=language,
                session_id=session_id,   # passed through for the DB race guard
            )

            if result["status"] == "success":
                update_session(session_id, stage="closed")
                history.append({"role": "model", "content": result["message"]})
                update_session(session_id, history=history)
                return result["message"]
            else:
                if "disponible" in result["message"].lower() or "available" in result["message"].lower():
                    # Créneau pris → régénérer les créneaux et remettre le flag à False
                    profile_fields["offered_slots"] = generate_available_slots()
                    profile_fields["slots_presented"] = False
                    update_session(session_id, profile_fields=profile_fields)
                history.append({"role": "model", "content": result["message"]})
                update_session(session_id, history=history)
                return result["message"]

        # 5. Créneaux pas encore présentés → les afficher
        translations = {
            "en": {
                "intro": "I have a few time slots available for a 15-minute call to discuss your project in detail.",
                "ask": "Please pick a slot by number (1, 2, 3, ...) or say the day and time (e.g., 'Thursday 10am')."
            },
            "fr": {
                "intro": "J'ai quelques créneaux disponibles pour un appel de 15 minutes afin de discuter de votre projet en détail.",
                "ask": "Veuillez choisir un créneau par numéro (1, 2, 3, ...) ou indiquer le jour et l'heure (ex: 'jeudi 10h')."
            },
            "ar": {
                "intro": "لدي بعض المواعيد المتاحة لمكالمة مدتها 15 دقيقة لمناقشة مشروعك بالتفصيل.",
                "ask": "الرجاء اختيار موعد برقم (1، 2، 3، ...) أو ذكر اليوم والوقت (مثال: 'الخميس 10 صباحاً')."
            }
        }
        t = translations.get(language, translations["en"])
        slot_text = format_slots_for_prompt(available_slots)
        bot_reply = f"{t['intro']}\n\n{slot_text}\n\n{t['ask']}"

        profile_fields["slots_presented"] = True
        update_session(session_id, profile_fields=profile_fields)

        history.append({"role": "model", "content": bot_reply})
        update_session(session_id, history=history)
        return bot_reply

    # ─── Build system prompt ──────────────────────────────────────────
    system_prompt = _build_system_prompt(stage, language)

    # ─── Stage-appropriate temperature (from config) ──────────────────
    temperature = STAGE_TEMPERATURE.get(stage, 0.5)

    # ─── Call Gemini ──────────────────────────────────────────────────
    bot_reply = await asyncio.to_thread(
        call_gemini,
        system_prompt=system_prompt,
        user_message=user_message,
        history=history,
        temperature=temperature,
    )

    history.append({"role": "model", "content": bot_reply})
    update_session(session_id, history=history)

    return bot_reply


# ─── HELPER FUNCTIONS ──────────────────────────────────────────────────
def _build_system_prompt(stage: str, language: str) -> str:
    lang_label = language.upper() if language else "EN"

    stage_configs = {
        "greeting": {
            "task": "Greet the user warmly and ask ONE question: what do they need help with?",
            "forbidden": "Do not ask about budget, timeline, or contact yet.",
        },
        "need": {
            "task": "Ask the user to briefly describe what they need (e.g., a website, an automation tool, a booking system).",
            "forbidden": "Do not ask about budget, timeline, or contact yet. Do not ask follow-up questions about their business.",
        },
        "budget": {
            "task": "Ask the user for their rough budget range. A ballpark is fine.",
            "forbidden": "Do not ask about timeline or contact. Do not ask why they have that budget or how they arrived at it.",
        },
        "timeline": {
            "task": "Ask the user when they need this done — a rough timeline is enough.",
            "forbidden": "Do not ask about contact details. Do not ask why the deadline exists or what happens if it's missed.",
        },
        "contact": {
            "task": "Ask the user for their best contact email so the team can follow up.",
            "forbidden": "Do not ask for phone numbers, LinkedIn, or any other contact form. One email is enough.",
        },
        "scoring": {
            "task": "Acknowledge the user briefly. Say you have everything you need.",
            "forbidden": "Do not ask any questions. Do not summarise the conversation. One sentence only.",
        },
        "booking": {
            "task": (
                "Explain that these are time slots for a 15‑minute call with the business owner "
                "to discuss the project in detail. Then present the available slots and ask "
                "the user to pick one by number (1, 2, or 3)."
            ),
            "forbidden": (
                "CRITICAL — do NOT: ask discovery questions, ask about their team or POS system, "
                "ask about their decision‑making process, ask about risks or deadlines, "
                "pretend to send calendar invites (you cannot), or say anything other than "
                "presenting the slots and asking for a number."
            ),
        },
        "closed": {
            "task": "Thank the user and say goodbye. One or two sentences only.",
            "forbidden": "Do not ask any questions. Do not offer more help. The conversation is over.",
        },
    }

    config = stage_configs.get(stage, {
        "task": "Ask the user about their needs.",
        "forbidden": "Do not ask multiple questions at once.",
    })

    prompt = f"""You are a lead qualification assistant for a small business.
Reply in {lang_label}.

YOUR TASK: {config["task"]}

RULES:
- Reply in 1–3 short sentences maximum.
- Ask at most ONE question per reply.
- Never ask for information you already have.
- Never invent actions you cannot perform (sending emails, booking meetings, etc.).

FORBIDDEN: {config["forbidden"]}"""

    return prompt.strip()


def _is_complete(lead_profile: LeadProfile) -> bool:
    fields = {
        "language": lead_profile.language,
        "need": lead_profile.need,
        "budget": lead_profile.budget,
        "timeline": lead_profile.timeline,
        "contact": lead_profile.contact,
    }
    for field in CRITICAL_FIELDS:
        value = fields.get(field, "")
        if not value or value.strip() == "":
            return False
    return True


def _get_next_stage(current_stage: str, lead_profile: LeadProfile) -> str:
    stage_to_field = {
        "need": "need",
        "budget": "budget",
        "timeline": "timeline",
        "contact": "contact",
    }
    if current_stage in ("greeting", "need"):
        if lead_profile.need and lead_profile.budget and lead_profile.timeline:
            return "contact"
        elif lead_profile.need and lead_profile.budget:
            return "timeline"
        elif lead_profile.need:
            return "budget"
    if current_stage in stage_to_field:
        field = stage_to_field[current_stage]
        if getattr(lead_profile, field, ""):
            try:
                current_index = STAGES.index(current_stage)
                if current_index + 1 < len(STAGES):
                    return STAGES[current_index + 1]
            except ValueError:
                pass
    return current_stage


# ─── LEAD PROFILE EXTRACTION ──────────────────────────────────────────
async def _update_lead_profile(
    session_id: str,
    history: list,
    language: str,
    profile_fields: dict,
) -> tuple[LeadProfile, dict]:
    extracted = await asyncio.to_thread(extract_lead_profile, history)
    merged = {
        "need": (extracted.need if extracted and extracted.need else profile_fields.get("need", "")),
        "budget": (extracted.budget if extracted and extracted.budget else profile_fields.get("budget", "")),
        "timeline": (extracted.timeline if extracted and extracted.timeline else profile_fields.get("timeline", "")),
        "contact": (extracted.contact if extracted and extracted.contact else profile_fields.get("contact", "")),
    }
    if extracted and extracted.budget_usd_estimate is not None:
        profile_fields["budget_usd_estimate"] = extracted.budget_usd_estimate
    if extracted and extracted.timeline_weeks_estimate is not None:
        profile_fields["timeline_weeks_estimate"] = extracted.timeline_weeks_estimate
    profile_fields.update(merged)
    update_session(session_id, profile_fields=profile_fields)
    lead_profile = LeadProfile(
        language=language,
        need=merged["need"],
        budget=merged["budget"],
        timeline=merged["timeline"],
        contact=merged["contact"],
        score="unknown",
        stage="greeting",
    )
    return lead_profile, profile_fields
