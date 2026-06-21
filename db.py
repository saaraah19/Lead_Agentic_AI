# ─── IMPORTS ──────────────────────────────────────────────────────────
# WHAT: sqlite3 is built into Python — no extra installs.
# WHY: We use it because it's a single file, zero setup, perfect for a 3‑day build.
import sqlite3

# WHAT: json lets us store Python lists/dicts as text in SQLite.
# WHY: SQLite has no native list type — we convert lists to JSON strings and back.
import json

# WHAT: datetime gives us timestamps for created_at and updated_at.
# WHY: We want to know *when* the lead came in and *when* it was last updated.
from datetime import datetime

# WHAT: contextmanager is a decorator that turns a function into a "with" block.
# WHY: It guarantees the database connection always closes, even if an error happens.
from contextlib import contextmanager

# ─── CONFIG ──────────────────────────────────────────────────────────
# WHAT: The name of the single file that holds everything.
# WHY: All tables live in this one file. Easy to backup, easy to delete for a fresh start.
DB_FILENAME = "leads.db"

# ─── CONTEXT MANAGER (the safe way to talk to the database) ──────────
@contextmanager
def get_db():
    """
    WHAT: Open the database file, give you a connection, and ALWAYS close it.
    WHY: If an exception happens inside the 'with' block, the connection still closes.
         This prevents "connection leaks" (too many open files).
    HOW TO USE:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ...")
            # connection auto-closes here, even if an error occurs

    STEP BY STEP:
        1. Open the connection to leads.db.
        2. Switch SQLite to WAL (Write-Ahead Logging) mode.
        3. Set row_factory so we can access columns by name (row["language"]).
        4. Try to 'yield' the connection to the caller (the code inside the 'with' block).
        5. After the 'with' block finishes (or even if it crashes), ALWAYS close the connection.

    FIX 9 — WHY WAL MODE:
        These functions run synchronously inside async route handlers
        (FastAPI/agent.py), which technically blocks the event loop for
        the duration of each call. In practice that's a few hundred
        microseconds per query — nothing close to the multi-second cost
        of a Gemini API round trip — so the real risk under concurrent
        load isn't per-call latency, it's *lock contention*: SQLite's
        default journal mode takes an exclusive lock on writes, so two
        simultaneous conversations updating their sessions at the same
        moment would serialize and one would wait. WAL mode lets reads
        proceed concurrently with a write, which covers the vast majority
        of contention this app would see (many sessions reading/updating
        their own independent rows). It's set with PRAGMA on every
        connection — cheap, and a no-op after the first time since it's
        persisted in the database file itself.
        For genuinely high traffic (many concurrent writers to the *same*
        row, or so much volume that even WAL's write serialization is a
        bottleneck), the real fix is migrating to aiosqlite or Postgres —
        see the commented-out aiosqlite line in requirements.txt.
    """
    conn = sqlite3.connect(DB_FILENAME)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row   # Enables: row["language"] instead of row[1]
    try:
        yield conn   # Give the connection to the caller
    finally:
        conn.close() # This ALWAYS runs — even if the caller crashes

# ─── INIT: CREATE TABLES (runs once when the app starts) ────────────
def init_db():
    """
    WHAT: Create the 3 tables (sessions, leads, bookings) if they don't exist.
    WHY: If the database file is new, this builds the structure.
         If it already exists, this does nothing (keeps existing data).
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # ─── TABLE 1: sessions ──────────────────────────────────────
        # WHAT: Holds the "memory" of each active conversation.
        # WHY: When a visitor sends a message, we need to know:
        #      - What language are we speaking? (language)
        #      - What question are we on? (stage)
        #      - What answers have they given so far? (profile_fields)
        #      - What was already said? (history)
        #
        # COLUMNS EXPLAINED:
        #   - session_id: Unique ID for each conversation (generated in the browser).
        #   - language: "en", "ar", or "fr" — empty string "" until first message.
        #   - stage: "greeting", "need", "budget", "timeline", "contact", "scoring", "booking", "closed"
        #   - profile_fields: JSON string storing incremental answers.
        #       e.g., {"need":"Website", "budget":"", "timeline":"", "contact":""}
        #   - history: JSON array of every turn. e.g., [{"role":"user","content":"Hi"}, {"role":"model","content":"Hello"}]
        #   - created_at: When the session started.
        #   - updated_at: When the session was last changed.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                language TEXT,
                stage TEXT,
                profile_fields TEXT,
                history TEXT,
                created_at DATETIME,
                updated_at DATETIME
            )
        """)

        # ─── TABLE 2: leads ──────────────────────────────────────────
        # WHAT: Stores the FINAL lead profile after ALL questions are answered.
        # WHY: This is the "structured record" we export as CSV and send to Notion.
        #      One row per qualified lead (once the conversation finishes).
        #
        # COLUMNS EXPLAINED:
        #   - id: Auto-incrementing number (just a unique row ID).
        #   - session_id: Which conversation produced this lead. UNIQUE prevents duplicates.
        #   - language: Detected language.
        #   - need: What they need (e.g., "Website with ordering").
        #   - budget: Their budget (e.g., "$5,000+").
        #   - timeline: Their timeline (e.g., "2 weeks").
        #   - contact: Email or phone number.
        #   - score: "hot", "warm", or "cold".
        #   - created_at: When the lead was saved.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE,   -- Prevents duplicate leads per session
                language TEXT,
                need TEXT,
                budget TEXT,
                timeline TEXT,
                contact TEXT,
                score TEXT,
                created_at DATETIME
            )
        """)

        # ─── TABLE 3: bookings ──────────────────────────────────────
        # WHAT: Stores confirmed calendar slots.
        # WHY: When a lead books a time, we save it here so we don't offer it to someone else.
        #
        # COLUMNS EXPLAINED:
        #   - id: Auto-incrementing number.
        #   - session_id: Which lead booked this slot.
        #   - slot_time: e.g., "2026-06-20 14:00:00"
        #   - created_at: When the booking was made.
        #
        # FIX 7: slot_time is now UNIQUE.
        # WHY: booking.py checks get_booked_slots() before offering slots,
        #      but that check and the eventual save_booking() insert are
        #      two separate steps with a gap between them. If two users are
        #      offered the same slot and both confirm within that gap,
        #      nothing previously stopped both inserts from succeeding —
        #      a real double-booking. The UNIQUE constraint makes the
        #      database itself the source of truth: the second insert now
        #      fails loudly (sqlite3.IntegrityError) instead of silently
        #      succeeding, and save_booking() below catches that and
        #      reports it back to the caller as "slot just got taken."
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                slot_time TEXT UNIQUE,
                created_at DATETIME
            )
        """)

        conn.commit()   # Save all table creations to the database file
        print("✅ Database initialized with tables: sessions, leads, bookings")

# ─── SESSIONS: CREATE A NEW BLANK SESSION ─────────────────────────────
def create_session(session_id):
    """
    WHAT: Insert a new blank session for a visitor who just started chatting.
    WHY: First time a visitor sends a message, we need a record to track their conversation.

    SETS THESE DEFAULTS:
        - language = ""  (empty string, NOT None — matches model.py's default)
        - stage = "greeting" (starts at the very beginning)
        - profile_fields = {"need":"", "budget":"", "timeline":"", "contact":""} (all empty)
        - history = [] (empty list, no messages yet)
        - created_at = now
        - updated_at = now

    FIX 6: uses INSERT OR IGNORE instead of a plain INSERT.
    WHY: agent.py calls this as `if not session: create_session(...)`. Two
         near-simultaneous requests carrying the same brand-new session_id
         (e.g. a double-click, or a client retry) can both read "no
         session exists" before either one's INSERT commits — the second
         INSERT then hits the session_id PRIMARY KEY constraint and raises
         sqlite3.IntegrityError, which previously was not caught here and
         would bubble up as a 500. OR IGNORE makes the second call a safe
         no-op instead: whichever insert wins, the caller re-reads with
         get_session() right after, so they end up with the same row
         either way.
    """
    now = datetime.now().isoformat()   # e.g., "2026-06-18T14:30:00"

    # ─── The profile_fields as a Python dict, then converted to JSON ───
    # WHAT: We store incremental answers here as the user replies.
    # WHY: When the user says "I need a website", we update profile_fields["need"] = "Website".
    #      This way we don't have to re-read the entire history to find the answer later.
    default_fields = json.dumps({
        "need": "",
        "budget": "",
        "timeline": "",
        "contact": ""
    })

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO sessions (session_id, language, stage, profile_fields, history, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            "",                     # language: empty until first message
            "greeting",             # stage: start at the beginning
            default_fields,         # profile_fields: all empty (as JSON string)
            json.dumps([]),         # history: empty list (as JSON string)
            now,                    # created_at
            now                     # updated_at
        ))
        conn.commit()   # Save the new row (or no-op if it already existed)

# ─── SESSIONS: LOAD AN EXISTING SESSION ───────────────────────────────
def get_session(session_id):
    """
    WHAT: Load a session from the database by its session_id.
    WHY: When a message arrives, we need to load the current state:
         - What language are we speaking?
         - What question are we on (stage)?
         - What answers have we collected so far (profile_fields)?
         - What was already said (history)?

    RETURNS: A Python dict with keys:
             session_id, language, stage, profile_fields (as dict),
             history (as list), created_at, updated_at.
             OR None if the session doesn't exist.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()   # Fetches the first matching row, or None

    if row:
        # ─── Convert JSON strings back to Python objects ────────────────
        # WHAT: The database stores profile_fields and history as JSON text.
        # WHY: We need to turn them back into Python dict/list to work with them.
        history = json.loads(row["history"]) if row["history"] else []
        profile_fields = json.loads(row["profile_fields"]) if row["profile_fields"] else {}

        return {
            "session_id": row["session_id"],
            "language": row["language"],
            "stage": row["stage"],
            "profile_fields": profile_fields,   # Now a Python dict
            "history": history,                 # Now a Python list
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    return None   # Session not found

# ─── SESSIONS: UPDATE AN EXISTING SESSION ─────────────────────────────
def update_session(session_id, language=None, stage=None, profile_fields=None, history=None):
    """
    WHAT: Update one or more fields of an existing session.
    WHY: After each bot reply, we need to save:
         - The new stage (e.g., moved from "budget" to "timeline")
         - Any new answers (updated profile_fields)
         - The updated history (appended the latest turn)

    HOW IT WORKS:
        1. We build a list of fields to update dynamically.
        2. We only update the fields that the caller passed in (not None).
        3. We ALWAYS update the updated_at timestamp.
        4. We execute the UPDATE query.

    EXAMPLE:
        update_session("abc123", stage="budget", profile_fields={"need":"Website", ...})
        → Updates only the 'stage' and 'profile_fields' columns, leaves 'language' and 'history' unchanged.
    """
    updates = []
    values = []

    # ─── Add each field that was provided ──────────────────────────────
    if language is not None:
        updates.append("language = ?")
        values.append(language)

    if stage is not None:
        updates.append("stage = ?")
        values.append(stage)

    if profile_fields is not None:
        # WHAT: Convert the Python dict to a JSON string before storing.
        # WHY: SQLite can't store Python dicts directly.
        updates.append("profile_fields = ?")
        values.append(json.dumps(profile_fields))

    if history is not None:
        # WHAT: Convert the Python list to a JSON string before storing.
        # WHY: SQLite can't store Python lists directly.
        updates.append("history = ?")
        values.append(json.dumps(history))

    # ─── Always update the updated_at timestamp ────────────────────────
    # WHY: We always want to know when the session was last changed.
    updates.append("updated_at = ?")
    values.append(datetime.now().isoformat())

    # ─── Add the session_id at the end for the WHERE clause ────────────
    values.append(session_id)

    # ─── Build and execute the query ───────────────────────────────────
    # WHAT: Dynamically creates: "UPDATE sessions SET language = ?, stage = ?, updated_at = ? WHERE session_id = ?"
    # WHY: This is cleaner than writing separate UPDATE statements for every combination.
    query = f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, values)
        conn.commit()   # Save the changes

# ─── LEADS: SAVE THE FINAL PROFILE ────────────────────────────────────
def save_lead(session_id, lead_profile):
    """
    WHAT: Save the completed LeadProfile (from model.py) to the leads table.
    WHY: Once the agent has gathered all critical fields, we store it permanently.
         This is the record we export as CSV and send to Notion.

    ARG: lead_profile is an instance of the LeadProfile Pydantic class.
         It has attributes: language, need, budget, timeline, contact, score.

    NOTE: The UNIQUE constraint on session_id prevents duplicate rows.
          If we accidentally call this twice for the same session, it will raise an error
          (instead of silently creating a duplicate record).
    """
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        # FIX 4: INSERT OR IGNORE — if this session_id already has a lead row
        # (e.g. agent retried after a network blip), the second call silently
        # skips instead of raising IntegrityError. The try/except in agent.py
        # is the first line of defence; this is the DB-layer backstop.
        cursor.execute("""
            INSERT OR IGNORE INTO leads (session_id, language, need, budget, timeline, contact, score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            lead_profile.language,   # e.g., "en"
            lead_profile.need,       # e.g., "Website with ordering"
            lead_profile.budget,     # e.g., "$5,000+"
            lead_profile.timeline,   # e.g., "2 weeks"
            lead_profile.contact,    # e.g., "sarah@example.com"
            lead_profile.score,      # e.g., "hot"
            now
        ))
        conn.commit()

# ─── LEADS: GET ALL LEADS FOR EXPORT ──────────────────────────────────
def get_all_leads():
    """
    WHAT: Fetch every lead from the leads table.
    WHY: Used by the CSV export endpoint (/leads/export) to generate a report.

    RETURNS: A list of Python dicts, one per lead. Each dict has keys:
             id, session_id, language, need, budget, timeline, contact, score, created_at.

    ORDER: Newest leads first (ORDER BY created_at DESC).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM leads ORDER BY created_at DESC")
        rows = cursor.fetchall()

    # ─── Convert each row (sqlite3.Row) to a plain Python dict ─────────
    # WHY: dict(row) turns the row into {"id":1, "session_id":"abc", ...}
    #      This makes it easy to pass to csv.DictWriter later.
    return [dict(row) for row in rows]

# ─── BOOKINGS: SAVE A CONFIRMED SLOT ──────────────────────────────────
def save_booking(session_id, slot_time):
    """
    WHAT: Store a confirmed booking.
    WHY: When a visitor picks a time slot, we record it so we don't offer it to someone else.

    slot_time format: e.g., "2026-06-20 14:00:00"

    RETURNS: True if the booking was saved, False if the slot was just
             taken by someone else (UNIQUE constraint violation).

    FIX 7 (paired with the UNIQUE constraint on bookings.slot_time above):
        Two users can be offered the same slot in the small window between
        "we checked which slots are free" and "this user confirmed one."
        Without this catch, the second confirmation would crash with an
        uncaught sqlite3.IntegrityError. Now it returns False so the
        caller (booking.confirm_booking) can tell the user the slot was
        just taken instead of pretending the booking succeeded.
    """
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO bookings (session_id, slot_time, created_at)
                VALUES (?, ?, ?)
            """, (session_id, slot_time, now))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Someone else booked this exact slot_time first.
            return False

# ─── BOOKINGS: CHECK WHICH SLOTS ARE ALREADY TAKEN ────────────────────
def get_booked_slots():
    """
    WHAT: Fetch all slot_times that are already booked.
    WHY: When we generate available slots in booking.py, we need to exclude the ones already taken.

    RETURNS: A Python SET of strings like {"2026-06-20 14:00:00", "2026-06-21 10:00:00"}
             Using a SET makes it very fast to check if a slot is taken.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT slot_time FROM bookings")
        rows = cursor.fetchall()

    # ─── Set comprehension ──────────────────────────────────────────────
    # WHAT: Loop through the rows and extract the slot_time from each.
    # WHY: A set is perfect for checking membership: "if slot in booked_slots:"
    booked = {row["slot_time"] for row in rows}
    return booked

# ─── AUTO-INIT (this runs when the file is imported) ──────────────────
# WHAT: Call init_db() automatically so the tables are ready as soon as the app starts.
# WHY: We don't want to remember to call it manually — it just happens.
#      If the database file is new, it creates the tables.
#      If the database file already exists, it does nothing.
init_db()