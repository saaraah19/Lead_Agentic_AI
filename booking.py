# ─── IMPORTS ──────────────────────────────────────────────────────────
# WHAT: datetime and timedelta let us generate future dates/times.
# WHY: We need to create slots for the next few days.
from datetime import datetime, timedelta

# WHAT: re (regex) helps us parse the user's booking choice.
# WHY: The user might say "2pm", "slot 3", "Tuesday", etc.
import re

# WHAT: Import the database functions for storing and checking bookings.
# WHY: We need to save confirmed bookings and exclude already‑taken slots.
from db import save_booking, get_booked_slots

# WHAT: Import the booking config from config.py.
# WHY: How many slots to generate (days ahead, slots per day).
from config import BOOKING_SLOT_COUNT

# ─── GENERATE AVAILABLE SLOTS ──────────────────────────────────────────
def generate_available_slots() -> list:
    """
    WHAT: Generate a list of available time slots for the next few days.
    WHY: We offer these to the lead so they can book a call.

    RULES (v1):
        - Start from tomorrow (skip today, to give the business owner time to prepare).
        - Only weekdays (Monday–Friday) — skip weekends.
        - Generate 2 slots per day: 10:00 AM and 2:00 PM.
        - Generate up to BOOKING_SLOT_COUNT slots total (from config.py).
        - Skip any slots that are already booked (check db.get_booked_slots()).

    RETURNS: A list of dicts, each with:
        - 'slot': The ISO datetime string (e.g., "2026-06-20 10:00")
        - 'display': A human‑readable string (e.g., "Tue 20 Jun 10:00 AM")
        - 'index': A number (1-based) for easy reference.

    EXAMPLE OUTPUT:
        [
            {"slot": "2026-06-20 10:00", "display": "Tue 10:00 AM", "index": 1},
            {"slot": "2026-06-20 14:00", "display": "Tue 02:00 PM", "index": 2},
            {"slot": "2026-06-21 10:00", "display": "Wed 10:00 AM", "index": 3},
        ]
    """
    # ─── Step 1: Get already‑booked slots ─────────────────────────────
    # WHAT: Fetch all slot times that are already taken.
    # WHY: We must not offer a slot that's already booked.
    booked_slots = get_booked_slots()

    # ─── Step 2: Generate candidate slots ─────────────────────────────
    available_slots = []
    index = 1

    # ─── Step 2a: Start from tomorrow ─────────────────────────────────
    # WHAT: We don't offer today — the owner needs time to prepare.
    # WHY: If we offer today, the owner might not see the alert in time.
    start_date = datetime.now().date() + timedelta(days=1)

    # ─── Step 2b: Loop through days until we have enough slots ────────
    # WHAT: Keep adding days until we have BOOKING_SLOT_COUNT slots.
    # WHY: We generate exactly the number of slots specified in config.
    current_date = start_date
    slots_generated = 0

    # ─── Safety limit: don't loop forever ──────────────────────────────
    # WHAT: We only loop up to 14 days ahead (2 weeks).
    # WHY: If we can't find enough slots (e.g., all booked), we stop.
    max_days_ahead = 14
    days_checked = 0

    while slots_generated < BOOKING_SLOT_COUNT and days_checked < max_days_ahead:
        # ─── Step 2c: Skip weekends ─────────────────────────────────────
        # WHAT: Check if it's Saturday (5) or Sunday (6).
        # WHY: The business owner probably doesn't work weekends.
        if current_date.weekday() in [5, 6]:   # 5=Sat, 6=Sun
            current_date += timedelta(days=1)
            days_checked += 1
            continue

        # ─── Step 2d: Generate time slots for this day ──────────────────
        # WHAT: Create two slots: 10:00 AM and 2:00 PM.
        # WHY: These are reasonable call times for a business owner.
        time_slots = ["10:00", "14:00"]

        for time_str in time_slots:
            # ─── Build the full slot datetime string ────────────────────
            # WHAT: Combine the date and time into "YYYY-MM-DD HH:MM".
            # WHY: This is a clean, sortable format for storage and comparison.
            slot_datetime = f"{current_date.isoformat()} {time_str}"

            # ─── Skip if already booked ─────────────────────────────────
            # WHAT: Check if this slot is already in the booked_slots set.
            # WHY: We don't want to offer a slot that's already taken.
            if slot_datetime in booked_slots:
                continue

            # ─── Format the display string ──────────────────────────────
            # WHAT: Create a human‑readable version for the user.
            # WHY: "Tue 10:00 AM" is nicer than "2026-06-20 10:00".
            #      We parse the datetime object to get the day name.
            day_name = current_date.strftime("%a")   # e.g., "Tue"
            display = f"{day_name} {time_str}"
            # Convert 14:00 to 02:00 PM for readability
            if time_str == "14:00":
                display = display.replace("14:00", "02:00 PM")
            else:
                display = display.replace("10:00", "10:00 AM")

            # ─── Add the slot to our list ──────────────────────────────
            available_slots.append({
                "slot": slot_datetime,
                "display": display,
                "index": index
            })

            index += 1
            slots_generated += 1

            # ─── Stop if we've generated enough slots ───────────────────
            if slots_generated >= BOOKING_SLOT_COUNT:
                break

        # ─── Move to the next day ─────────────────────────────────────
        current_date += timedelta(days=1)
        days_checked += 1

    return available_slots


# ─── FORMAT SLOTS FOR GEMINI PROMPT ──────────────────────────────────
def format_slots_for_prompt(slots: list) -> str:
    """
    WHAT: Convert the list of slots into a clear, numbered string for Gemini.
    WHY: The bot needs to present the options to the user in a readable way.

    FORMAT:
        1. Tue 10:00 AM
        2. Tue 02:00 PM
        3. Wed 10:00 AM

    RETURNS: A string with each slot on a new line, numbered.
    """
    if not slots:
        return "No slots are currently available. Please try again later."

    lines = []
    for slot in slots:
        lines.append(f"{slot['index']}. {slot['display']}")

    return "\n".join(lines)


# ─── PARSE USER'S BOOKING CHOICE ─────────────────────────────────────
def parse_booking_choice(user_message: str, available_slots: list) -> str:
    """
    WHAT: Extract which slot the user wants from their message.
    WHY: The user might say "2pm", "slot 3", "Tuesday", or "the first one".
         We need to match it to one of our available slots.

    RULES (in order of priority):
        1. If they say a number (e.g., "1", "slot 2"), pick that index.
        2. If they say a time (e.g., "10am", "2pm"), match the time.
        3. If they say a day (e.g., "Tuesday"), match the day.
        4. If they say "first" / "last", pick the first/last slot.

    RETURNS: The selected slot string (e.g., "2026-06-20 10:00") or None if no match.
    """
    if not available_slots:
        return None

    user_msg = user_message.lower().strip()

    # ─── Rule 1: Check for a number (index) ──────────────────────────
    # WHAT: Look for a number between 1 and the number of slots.
    # WHY: User might say "slot 2", "pick 3", or just "2".
    for slot in available_slots:
        if re.search(r'\b' + str(slot['index']) + r'\b', user_msg):
            return slot['slot']

    # ─── Rule 2: Check for a time (e.g., "10am", "2pm", "14:00") ────────
    # WHAT: Look for an explicit time-of-day mention and match it against
    #       each slot's actual hour, respecting am/pm where given.
    # WHY (FIX 10 — found by the test suite, not by inspection):
    #      The original code defined `time_patterns` with an am/pm regex
    #      and an HH:MM regex but never actually used either of them —
    #      it only ever ran `re.findall(r'\b(\d{1,2})\b', user_msg)`.
    #      \b requires a transition between a word char and a non-word
    #      char; in "2pm" the "2" is immediately followed by the letter
    #      "p" (also a word char), so there's no boundary there and the
    #      regex silently finds nothing. "2pm works for me" therefore
    #      returned None even though it's an unambiguous time. It also
    #      meant am/pm was never actually read, so a bare "2" matched
    #      both a 2am-equivalent and a 2pm-equivalent slot identically.
    #      This version checks am/pm explicitly first (and converts to
    #      24-hour before comparing), then HH:MM, and only falls back to
    #      an ambiguous bare number last.
    am_pm_match = re.search(r'(\d{1,2})\s*(am|pm)', user_msg)
    if am_pm_match:
        hour = int(am_pm_match.group(1))
        meridiem = am_pm_match.group(2)
        if meridiem == 'pm' and hour != 12:
            hour += 12
        elif meridiem == 'am' and hour == 12:
            hour = 0
        for slot in available_slots:
            slot_hour = int(slot['slot'].split()[1].split(':')[0])
            if hour == slot_hour:
                return slot['slot']

    hhmm_match = re.search(r'\b(\d{1,2}):(\d{2})\b', user_msg)
    if hhmm_match:
        hour = int(hhmm_match.group(1))
        for slot in available_slots:
            slot_hour = int(slot['slot'].split()[1].split(':')[0])
            if hour == slot_hour:
                return slot['slot']

    # ─── Bare number fallback (no am/pm, no colon) ────────────────────
    # WHAT: A bare number like "2" or "10" is genuinely ambiguous between
    #       12-hour and 24-hour reading, so we accept either interpretation.
    numbers = re.findall(r'\b(\d{1,2})\b', user_msg)

    for num_str in numbers:
        num = int(num_str)
        # ─── Check if this number matches a slot time ──────────────────
        for slot in available_slots:
            slot_time = slot['slot'].split()[1]  # e.g., "10:00"
            slot_hour = int(slot_time.split(':')[0])
            # ─── Convert the slot hour to 12-hour format for comparison ──
            slot_hour_12 = slot_hour if slot_hour <= 12 else slot_hour - 12
            # ─── If the user's number matches the slot hour ──────────────
            if num == slot_hour or num == slot_hour_12:
                return slot['slot']

    # ─── Rule 3: Check for day names ──────────────────────────────────
    # FIX 11 (also found by the test suite): day_map mixed 3-letter
    # abbreviations ('mon', 'tue'...) with full words ('saturday',
    # 'sunday'), and matched with a trailing \b — which requires a
    # boundary immediately after "wed", so it could match the standalone
    # word "wed" but never "wednesday" (no boundary between "d" and the
    # following "n"). Using \w* after the abbreviation lets "wed" match
    # both "wed" and "wednesday"/"weds", and standardizing all seven
    # entries to abbreviations means Saturday/Sunday get the same
    # flexible matching the weekdays already had.
    day_map = {
        'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6
    }
    for day_name, day_number in day_map.items():
        if re.search(rf'\b{day_name}\w*\b', user_msg):
            # ─── Find the first slot that falls on this day ─────────────
            for slot in available_slots:
                slot_date = datetime.fromisoformat(slot['slot'].split()[0])
                if slot_date.weekday() == day_number:
                    return slot['slot']

    # ─── Rule 4: Check for "first" or "last" ─────────────────────────
    if 'first' in user_msg or '1st' in user_msg:
        return available_slots[0]['slot']
    if 'last' in user_msg:
        return available_slots[-1]['slot']

    # ─── No match found ──────────────────────────────────────────────
    return None


# ─── CONFIRM A BOOKING ───────────────────────────────────────────────
def confirm_booking(session_id: str, user_message: str, available_slots: list) -> dict:
    """
    WHAT: The main entry point — take the user's message, find the slot, and save it.
    WHY: Called by agent.py when the user responds to a booking offer.

    ARGS:
        - session_id: The current conversation ID.
        - user_message: What the user said (e.g., "I'll take slot 2").
        - available_slots: The list of slots we offered.

    RETURNS: A dict with:
        - 'success': True if booked, False if not.
        - 'slot': The slot string if booked, or None.
        - 'message': A human‑readable message for the user.

    EXAMPLE:
        If user says "2pm", we find the 2pm slot, save it, and return:
        {
            "success": True,
            "slot": "2026-06-20 14:00",
            "message": "You're booked for Tuesday at 2:00 PM!"
        }
    """
    # ─── Step 1: Parse the user's choice ──────────────────────────────
    selected_slot = parse_booking_choice(user_message, available_slots)

    if not selected_slot:
        # ─── No match found ─────────────────────────────────────────────
        # WHAT: The user said something we couldn't parse.
        # WHY: We return a helpful message asking them to pick a number.
        return {
            "success": False,
            "slot": None,
            "reason": "unparsed",
            "message": "I couldn't understand which slot you'd like. Please reply with a number (e.g., '1', '2', or '3')."
        }

    # ─── Step 2: Save the booking to the database ─────────────────────
    # WHAT: Call db.save_booking() to store the confirmation.
    # WHY: This prevents double‑booking and gives us a permanent record.
    #
    # FIX 7: save_booking() now returns False if this exact slot was
    # booked by someone else in the gap between us offering it and this
    # user confirming it (race condition, caught at the DB layer via a
    # UNIQUE constraint). We have to handle that here instead of assuming
    # success — otherwise we'd tell two different leads they're both
    # booked for the same call.
    booked = save_booking(session_id, selected_slot)
    if not booked:
        return {
            "success": False,
            "slot": None,
            "reason": "taken",
            "message": "Sorry, that slot was just booked by someone else. Please pick a different one."
        }

    # ─── Step 3: Find the display string for the response ─────────────
    # WHAT: We need a nice message to tell the user they're booked.
    # WHY: "You're booked for Tuesday at 2:00 PM!" is much nicer than "Booked at 2026-06-20 14:00".
    display_time = None
    for slot in available_slots:
        if slot['slot'] == selected_slot:
            display_time = slot['display']
            break

    if display_time:
        message = f"✅ You're booked for {display_time}! We'll see you then."
    else:
        message = f"✅ Booking confirmed for {selected_slot}."

    return {
        "success": True,
        "slot": selected_slot,
        "message": message
    }