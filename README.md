# Lead Agent AI

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.137-009688?logo=fastapi&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-2.5_Flash-4285F4?logo=google&logoColor=white)
![CI](https://img.shields.io/github/actions/workflow/status/YOUR_USERNAME/lead-agent-ai/ci.yml?label=CI)
![License](https://img.shields.io/badge/license-MIT-green)

> **[→ Live demo](https://your-app.onrender.com/widget)** · **[→ API docs](https://your-app.onrender.com/docs)**

An AI chatbot that qualifies inbound sales leads in **English, French, and Arabic**, scores them as hot/warm/cold, and books a call with the right ones — deployed as a chat widget that drops into any small-business website.

Built with FastAPI + Google Gemini, backed by SQLite, with Slack and Notion integrations for the business owner.

---

## The problem it solves

Small businesses lose leads to slow follow-up. This bot sits on a website 24/7, has a structured conversation in whatever language the visitor writes in, extracts the four things a salesperson actually needs (need, budget, timeline, contact), scores the lead automatically, and — for the worthwhile ones — offers a booking slot on the spot.

---

## How it works

The conversation is an explicit stage machine, not a freeform chat loop:

```
greeting → need → budget → timeline → contact → scoring → booking → closed
```

1. **Language detection** — first message determines en/fr/ar (Arabic via Unicode range, French via accent/keyword markers, English as default). The whole conversation stays in that language from there.

2. **Extraction** — every turn, a structured Gemini call reads the full conversation and extracts `need`, `budget`, `timeline`, `contact` — plus normalized numeric estimates (`budget_usd_estimate`, `timeline_weeks_estimate`) so "50 grand" / "نصف مليون" / "cinq cents euros" all become comparable numbers without regex gymnastics.

3. **Regex fallback** — if the Gemini extraction call fails, a regex extractor fills in what it can so a single API hiccup never stalls the conversation.

4. **Stage advancement** — once a stage's field is filled, the bot moves to the next one. Each stage gets a tightly scoped system prompt with an explicit *forbidden actions* list, so the LLM doesn't invent discovery questions it shouldn't be asking.

5. **Scoring** — once all fields are present, `scorer.py` applies simple, readable thresholds:
   - **Hot** — budget ≥ $1,000 **and** timeline ≤ 4 weeks
   - **Warm** — some budget or some urgency
   - **Cold** — neither

6. **Routing on score:**
   - Hot → instant Slack alert + call booking offered immediately
   - Warm → logged to Notion + call booking offered, softer framing
   - Cold → polite close, no booking

7. **Booking** — generates the next available weekday slots (10am/2pm, skipping already-taken ones), lets the user pick by number, time, or day name, and persists the confirmed slot to prevent double-booking including race conditions.

8. **Every lead** (hot, warm, or cold) is logged to Notion as a permanent CRM record.

---

## Architecture

```
widget (/widget)
    │  POST /chat { session_id, message }
    ▼
main.py (FastAPI + rate limiting)
    │
    ▼
agent.py  ──────────────────► generator.py ──► Gemini API
(stage machine,                                  conversation reply +
 scoring trigger,                                structured extraction
 booking trigger)
    │
    ├──► db.py (SQLite)
    │    sessions / leads / bookings
    │
    ├──► scorer.py
    │
    ├──► booking.py
    │
    └──► notifier.py ──► Slack webhook
                     └──► Notion API
```

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + Uvicorn | Async, fast, great DX for a portfolio piece |
| LLM | Gemini 2.5 Flash | Best-in-class at instruction following + multilingual |
| Validation | Pydantic v2 | Two schemas: strict business rules vs permissive extraction |
| Storage | SQLite + WAL mode | Single-file, zero-setup; WAL handles concurrent sessions |
| Notifications | Slack Webhooks + Notion API | What a real SMB owner would actually check |
| Rate limiting | slowapi | Per-IP limit on `/chat` to prevent Gemini cost abuse |
| Deployment | Docker + Render | One `docker build` and it's live |

---

## Project structure

```
.
├── main.py               # FastAPI app, routes, rate limiting, CSV export
├── agent.py              # Stage machine, prompt construction, orchestration
├── generator.py          # Gemini client: replies + structured extraction
├── model.py              # Pydantic schemas (LeadProfile, ExtractedLeadFields)
├── scorer.py             # Hot/warm/cold logic
├── booking.py            # Slot generation, free-text parsing, confirmation
├── notifier.py           # Slack + Notion integrations
├── db.py                 # SQLite layer (sessions, leads, bookings)
├── config.py             # Env vars, thresholds, stage list
├── static/
│   ├── widget.html       # Chat widget UI (/widget)
│   └── landing.html      # Portfolio landing page (/)
├── tests/
│   ├── conftest.py           # Isolated temp DB per test
│   ├── test_scorer.py        # 7 tests — hot/warm/cold thresholds
│   ├── test_agent_stages.py  # 9 tests — stage machine, field-skip logic
│   └── test_booking.py       # 8 tests — slot parsing, race condition guard
├── .env.example
├── Dockerfile
└── requirements.txt
```

---

## Setup

### Prerequisites
- Python 3.12+
- A Gemini API key (free tier works): [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- Optional: Slack webhook URL, Notion integration token + database ID

### Run locally

```bash
git clone https://github.com/YOUR_USERNAME/lead-agent-ai
cd lead-agent-ai

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
GEMINI_API_KEY=your_key_here
EXPORT_TOKEN=some-long-random-string   # required for /leads/export
SLACK_WEBHOOK_URL=                     # optional
NOTION_API_KEY=                        # optional
NOTION_DATABASE_ID=                    # optional
```

```bash
uvicorn main:app --reload
```

| URL | What you get |
|---|---|
| `http://localhost:8000/widget` | Chat widget demo |
| `http://localhost:8000/` | Landing page |
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/health` | JSON health check |

### Docker

```bash
docker build -t lead-agent-ai .
docker run -p 8000:8000 --env-file .env lead-agent-ai
```

---

## Testing

24 tests across the three areas most likely to hide a subtle bug: `score_lead` (hot/warm/cold thresholds, including Gemini-estimate vs regex-fallback paths), `_get_next_stage`/`_is_complete` (the stage machine), and `parse_booking_choice`/`confirm_booking` (free-text slot parsing and the race-condition guard). Writing these actually surfaced two real bugs that code review alone hadn't caught — documented below.

```bash
pytest tests/ -v
```

Each test runs against a throwaway SQLite file (`conftest.py` patches `db.DB_FILENAME` to a `tmp_path`), so the suite never touches your real `leads.db`.

---

## API

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/` | GET | — | Landing page |
| `/health` | GET | — | JSON health check (runs a real `SELECT 1`) |
| `/chat` | POST | — | Send a message, get the bot's reply |
| `/widget` | GET | — | Chat widget UI |
| `/leads/export` | GET | `X-Export-Token` header | Download all leads as CSV |
| `/docs` | GET | — | Auto-generated Swagger UI |

**`/chat` request body:**
```json
{ "session_id": "abc123", "message": "Hi, I need a website" }
```

**Rate limits:** `/chat` is capped at 20 req/min per IP; `/leads/export` at 10 req/min.

---

## Design decisions

**Stage-scoped prompts over one mega-prompt.** Each stage gets an explicit task and an explicit *forbidden actions* list. Without the latter, the LLM reliably "helps" by asking extra discovery questions a lean qualifier shouldn't be asking — asking about business risks during the budget stage, for example.

**Two separate Pydantic schemas.** `LeadProfile` enforces business rules (valid stage, valid language); `ExtractedLeadFields` is deliberately permissive. Mid-conversation, most fields are legitimately still blank, and returning `None` on extraction failure (rather than empty values) means a failed API call never silently overwrites previously known answers.

**LLM-based numeric normalization over regex.** Budget/timeline parsing used to be pure regex, which is an unbounded pattern-matching problem across three languages. Gemini is asked directly for a normalized USD/weeks estimate; regex is kept only as a fallback.

**`INSERT OR IGNORE` + try/except double-guard on lead saves.** A retried request must never crash the conversation or create duplicate leads — guarded at both the application layer and the DB layer.

**`/` serves HTML, `/health` serves JSON.** The root URL is what a potential client sees first. Infrastructure health checks get their own dedicated endpoint.

---

## Bugs fixed during development

- **`/chat` and `/leads/export` rate-limited** (20/min and 10/min per IP) — without this, a single client could run up the Gemini bill.
- **`create_session` race condition** — two near-simultaneous first messages with the same `session_id` crashed one with an uncaught `IntegrityError`. Now uses `INSERT OR IGNORE`.
- **Booking double-booking race** — `bookings.slot_time` is now `UNIQUE` at the DB layer; the second confirmation gets a clear "that slot was just taken" message and a refreshed list.
- **Unbounded conversation history** — capped to 16 turns (`MAX_HISTORY_TURNS`) before every Gemini call.
- **SQLite concurrency** — `get_db()` sets `PRAGMA journal_mode=WAL`, so reads and writes from concurrent sessions don't serialize on a single lock.
- **Logging** — `main.py` calls `logging.basicConfig()` explicitly; `/chat` error handler uses `exc_info=True` so tracebacks actually reach the logs.
- **`/health` runs a real `SELECT 1`** instead of always claiming `"online"`.
- **Two bugs in `parse_booking_choice`** surfaced by the test suite: "2pm" wasn't parsed (the am/pm regex was defined but never called), and "wednesday" didn't match (only 3-letter abbreviations were checked).
- **Stage variable stale-read** in the booking flow caused the bot to loop on "I have everything I need" instead of offering slots.

---

## Known limitations

- **SQLite is synchronous.** WAL mode reduces lock contention, but the calls still block the event loop. `aiosqlite` or Postgres is the right fix at real scale (see commented-out line in `requirements.txt`).
- **Widget embeds via `window.location.origin`.** To embed on a client's own domain, the `API_BASE` in `widget.html` needs to point to the deployed API URL explicitly.
- **Booking slots use server local time.** No timezone is communicated to the user — fine when server and client share a timezone, something to address for cross-timezone deployments.
- **Existing `leads.db` files won't pick up the `UNIQUE` constraint** on `bookings.slot_time` automatically — `CREATE TABLE IF NOT EXISTS` only applies to new databases. A live upgrade needs a one-time migration.
- **No structured error tracking.** Logs go to stdout only, which is fine for Render's log viewer, not great for being paged at 2am.
