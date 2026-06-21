# Lead Agent AI

An AI chatbot that qualifies inbound sales leads in **English, French, and Arabic**, scores them as hot/warm/cold, and automatically books a call with the ones worth talking to — built to be dropped into any small-business website as a chat widget.

Built with FastAPI + Google Gemini, backed by SQLite, with Slack and Notion integrations for the business owner.

---

## Why this exists

Small businesses lose leads to slow follow-up. This bot sits on a website 24/7, has a structured conversation in whatever language the visitor writes in, pulls out the four things a salesperson actually needs (need, budget, timeline, contact), scores the lead automatically, and — for the good ones — offers a booking slot on the spot. Hot leads also trigger an instant Slack alert.

## How it works

The conversation is modeled as an explicit stage machine, not a freeform chat loop:

```
greeting → need → budget → timeline → contact → scoring → booking → closed
```

1. **Language detection** — first message determines en/fr/ar (Arabic via Unicode range, French via accent/keyword markers, English as default), then the whole conversation stays in that language.
2. **Extraction** — every turn, a structured Gemini call (`extract_lead_profile`) reads the full conversation and pulls out `need`, `budget`, `timeline`, `contact` — plus *normalized numeric estimates* (`budget_usd_estimate`, `timeline_weeks_estimate`) so "50 grand" / "نصف مليون" / "cinq cents euros" all become comparable numbers without regex guesswork.
3. **Regex fallback** — if the Gemini extraction call fails, a regex-based extractor (`_fallback_extract`) fills in whatever it can, so a single API hiccup never stalls the conversation.
4. **Stage advancement** — once a stage's field is filled, the bot moves to the next one. Each stage gets its own tightly scoped system prompt with an explicit *forbidden actions* list, so the LLM doesn't free-associate into discovery questions it has no business asking.
5. **Scoring** — once all critical fields are present, `scorer.py` applies simple, explainable thresholds (budget ≥ $1,000 **and** timeline ≤ 4 weeks → hot; any signal → warm; neither → cold).
6. **Routing on score**:
   - **Hot** → instant Slack alert + offered a call immediately.
   - **Warm** → logged to Notion + offered a call, softer framing.
   - **Cold** → polite close, no booking offered.
7. **Booking** — generates the next available weekday slots (10am/2pm, skipping already-booked ones), lets the user pick by number/time/day, and persists the confirmed slot to prevent double-booking.
8. **Every lead** (hot, warm, or cold) is logged to Notion as a permanent CRM record; hot leads additionally ping Slack.

## Architecture

```
widget (static/widget.html)
        │  POST /chat { session_id, message }
        ▼
   main.py (FastAPI)
        │
        ▼
   agent.py  ───────────────► generator.py ──► Gemini API
   (stage machine,                │                 (conversation +
    scoring trigger,              │                  structured extraction)
    booking trigger)              │
        │                         │
        ▼                         ▼
   db.py (SQLite)            scorer.py
   sessions / leads /        booking.py
   bookings tables                │
        │                         ▼
        ▼                   notifier.py ──► Slack webhook
   /leads/export (CSV)                  └──► Notion API
```

## Tech stack

| Layer | Choice |
|---|---|
| API framework | FastAPI + Uvicorn |
| LLM | Google Gemini (`gemini-2.5-flash`) via `google-genai` |
| Validation | Pydantic v2 (separate schemas for business rules vs. permissive extraction) |
| Storage | SQLite (single-file, zero-setup) |
| Notifications | Slack Incoming Webhooks, Notion API |
| Deployment | Docker (non-root user, healthcheck, Railway-style `$PORT` binding) |

## Project structure

```
.
├── main.py        # FastAPI app, routes, CORS, rate limiting, CSV export
├── agent.py        # Stage machine, prompt construction, orchestration
├── generator.py    # Gemini client: conversational replies + structured extraction
├── model.py        # Pydantic schemas (LeadProfile, ExtractedLeadFields)
├── scorer.py        # Hot/warm/cold scoring logic
├── booking.py        # Slot generation, parsing, confirmation
├── notifier.py        # Slack + Notion integrations
├── db.py        # SQLite access layer (sessions, leads, bookings)
├── config.py        # Env vars, thresholds, stage list
├── static/widget.html    # Chat widget UI (served at /widget)
├── tests/        # pytest suite (scorer, stage machine, booking)
├── .github/workflows/ci.yml   # Lint + test on every push/PR
├── .env.example
├── Dockerfile
└── requirements.txt
```

## Setup

### Prerequisites
- Python 3.12+
- A Gemini API key
- (Optional) Slack webhook URL, Notion integration token + database ID

### Local

```bash
git clone <your-repo-url>
cd lead-agent-ai
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file:

```env
GEMINI_API_KEY=your_key_here
SLACK_WEBHOOK_URL=               # optional
NOTION_API_KEY=                  # optional
NOTION_DATABASE_ID=              # optional
EXPORT_TOKEN=some-long-random-string   # required to use /leads/export
```

Run it:

```bash
uvicorn main:app --reload
```

Visit `http://localhost:8000/widget` to chat with the bot, or `http://localhost:8000/docs` for the interactive API docs.

### Docker

```bash
docker build -t lead-agent-ai .
docker run -p 8000:8000 --env-file .env lead-agent-ai
```

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Health check |
| `/chat` | POST | Send a message, get the bot's reply. Body: `{ "session_id": "...", "message": "..." }` |
| `/widget` | GET | Serves the chat widget UI |
| `/leads/export` | GET | Download all leads as CSV. Requires `X-Export-Token` header matching `EXPORT_TOKEN` |
| `/docs` | GET | Auto-generated Swagger UI |

## Design decisions worth knowing

- **Stage-scoped prompts over one mega-prompt.** Each stage gets an explicit task and an explicit forbidden-actions list. Without the latter, the LLM reliably "helps" by asking extra discovery questions a lean qualifier shouldn't be asking.
- **Two separate Pydantic schemas.** `LeadProfile` enforces business rules (valid stage, valid language); `ExtractedLeadFields` is deliberately permissive because mid-conversation, most fields are legitimately still blank — and returns `None` rather than empty values on extraction failure, so a failed API call never overwrites previously known answers with blanks.
- **LLM-based numeric normalization over regex.** Budget/timeline parsing used to be pure regex, which is an unbounded pattern-matching problem across three languages ("50 grand", "نصف مليون", "a few hundred bucks"). Gemini is asked directly for a normalized USD/weeks estimate; regex is kept only as a fallback for when extraction fails.
- **`INSERT OR IGNORE` + try/except double-guard on lead saves.** A retried request (network blip, client resend) must never crash the conversation or create duplicate leads — guarded at both the application layer and the DB layer.
- **CORS is wide open, credential-less.** `allow_origins=["*"]` with `allow_credentials=False` is intentional: the widget needs to be embeddable on arbitrary client domains, and since no cookies/sessions are used, the usual wildcard-CORS risk doesn't apply. Lock `allow_origins` down to specific client domains in production.

## Testing

```bash
pip install -r requirements.txt
pytest tests/ -v
```

28 tests cover the three functions most likely to hide a subtle bug: `score_lead` (hot/warm/cold thresholds, including the Gemini-estimate vs. regex-fallback paths), `_get_next_stage`/`_is_complete` (the stage machine), and `parse_booking_choice`/`confirm_booking` (free-text slot parsing and the booking race condition). Writing these actually surfaced two real bugs that code review alone hadn't caught — see "Fixed" below.

A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the suite plus a `ruff` lint pass on every push/PR.

## Fixed (since the first pass)

- **`/chat` and `/leads/export` now rate-limited** (20/min and 10/min per IP via `slowapi`), so a single client can't run up the Gemini bill or hammer the export endpoint.
- **`create_session` race condition** — two near-simultaneous first messages with the same `session_id` used to crash one of them with an uncaught `IntegrityError`. Now uses `INSERT OR IGNORE`, same pattern already used for `save_lead`.
- **Booking double-booking race** — `bookings.slot_time` is now `UNIQUE` at the DB layer; if two leads are offered the same slot and both confirm, the second gets a clear "that slot was just taken" message and a refreshed slot list, instead of two people silently booking the same call.
- **Unbounded conversation history** — capped to the most recent 16 turns (`MAX_HISTORY_TURNS` in `config.py`) before every Gemini call, so a long conversation doesn't mean growing latency/cost forever.
- **SQLite concurrency** — `get_db()` now sets `PRAGMA journal_mode=WAL`, so reads and writes from concurrent conversations don't serialize on a single lock the way SQLite's default journal mode would.
- **Logging** — `main.py` now calls `logging.basicConfig()` explicitly (consistent format everywhere) and the `/chat` error handler logs with `exc_info=True` so tracebacks actually make it into the logs.
- **`/` health check** now runs a real `SELECT 1` against the DB and reports `"degraded"` if it fails, instead of always claiming `"online"`.
- **Two real parsing bugs in `parse_booking_choice`**, found by writing tests rather than by inspection: it couldn't parse `"2pm"` (the `am`/`pm` regex was defined but never actually called — a bare `\b\d{1,2}\b` can't match the `2` in `2pm` because there's no word boundary between a digit and the following letter), and it couldn't match full day names like `"wednesday"` (it only matched the literal 3-letter abbreviation with a trailing boundary, which never fires inside a longer word).
- **`.env.example`** added so the required/optional env vars are obvious without reading `config.py`.

## Known limitations / what I'd still do next

- **No auth on `/chat` beyond rate limiting.** Rate limiting stops cost abuse but doesn't stop someone from scripting a conversation through it. Fine for an embedded widget behind your own domain; add an API key or origin check if this ever needs to be more locked-down.
- **SQLite is still synchronous, just less lock-contentious.** WAL mode meaningfully reduces blocking under concurrent load, but the calls are still blocking Python calls inside `async def` functions. Acceptable at small-to-medium traffic; `aiosqlite` or Postgres is the real fix at real scale (pencilled into `requirements.txt`).
- **Existing `leads.db` files won't pick up the new `UNIQUE` constraint automatically** — `CREATE TABLE IF NOT EXISTS` only applies to brand-new databases. If you're upgrading a live deployment rather than starting fresh, you'd need a small migration (recreate the `bookings` table) to get the double-booking protection on an existing DB file.
- **Test coverage is a start, not complete.** `agent.process_message` itself (the main orchestration function) isn't covered end-to-end — that would need mocking the Gemini client, which is a reasonable next addition.
- **No structured error tracking** (Sentry, etc.) — logs go to stdout only, fine for Railway's log viewer, not great for being paged at 2am.

## License

MIT (or your choice — add a `LICENSE` file).
