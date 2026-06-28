# ─── IMPORTS ──────────────────────────────────────────────────────────
# WHAT: FastAPI is the web framework — handles routes, requests, responses.
# WHY: We need a web server to listen for messages from the chat widget.
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# WHAT: Pydantic validates incoming JSON requests.
# WHY: Ensures the widget sends valid data.
from pydantic import BaseModel, Field

# WHAT: slowapi gives us per-IP rate limiting.
# WHY: /chat calls Gemini (costs money per request) and /leads/export reads
#      the whole leads table — both need a ceiling on how often any single
#      client can hit them. Without this, anyone who finds the URL can run
#      up the Gemini bill or hammer the export endpoint.
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# WHAT: Our own modules.
# WHY: agent.py handles conversation logic, db.py gives us lead data.
from agent import process_message
from db import get_all_leads, get_db

# WHAT: Built-in libraries for generating CSV files.
# WHY: Convert lead data into a downloadable file.
import csv
import io
import os
import logging

# ─── LOGGING ────────────────────────────────────────────────────────
# WHAT: Configure the root logger once, here, at the entry point.
# WHY: Every module in this project calls logging.getLogger(__name__),
#      but nothing previously called basicConfig() anywhere — so output
#      format/level depended entirely on whatever Uvicorn happened to set
#      up. Configuring it explicitly means logs look the same locally,
#      in Docker, and on Railway.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─── RATE LIMITER ───────────────────────────────────────────────────
# WHAT: One shared Limiter instance, keyed by client IP.
# WHY: Cheap, no extra infra (in-memory by default — fine for a single
#      instance; swap storage_uri for Redis if you ever scale to
#      multiple workers/instances).
limiter = Limiter(key_func=get_remote_address)

# ─── APP SETUP ──────────────────────────────────────────────────────────
app = FastAPI(
    title="Lead Qualification Agent",
    description="AI chatbot that qualifies leads in multiple languages (Arabic, French, English)",
    version="1.0.0"
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─── CORS MIDDLEWARE ──────────────────────────────────────────────────
# WHAT: CORS allows the widget to be embedded on websites.
# WHY: Without this, browsers block cross-origin requests.
#
# SECURITY NOTE: In production, replace "*" with your client's actual domain.
# The combination of allow_origins=["*"] + allow_credentials=True is risky
# because it tells browsers to send cookies to any site. Since we don't use
# cookies/sessions, we can safely set allow_credentials=False.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],              # For production: ["https://client-site.com"]
    allow_credentials=False,          # Changed from True — we don't use cookies
    allow_methods=["*"],              # Allow GET, POST, etc.
    allow_headers=["*"],              # Allow all headers
)

# ─── SERVE STATIC FILES ──────────────────────────────────────────────
# WHAT: Mount the "static" folder so files are accessible via /static/...
# WHY: Separates UI (HTML/CSS/JS) from backend logic.
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── REQUEST MODEL ──────────────────────────────────────────────────
class ChatRequest(BaseModel):
    # FIX 5: validate both fields at the API boundary so bad inputs
    # never reach the agent. An empty message would cause a Gemini
    # call with no content; a 100k message is a DoS vector.
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=2000)

# ─── ROUTE 1: HOME (Landing Page) ───────────────────────────────────
# WHAT: Serve the portfolio landing page at the root URL.
# WHY: When a client visits the deployed Render URL, they should see a
#      professional product page — not a raw JSON blob. The landing page
#      (static/landing.html) shows what the agent does, provides a live
#      demo link (/widget), and links to the API docs. The actual health
#      check data is still available at /health for infrastructure checks.
@app.get("/", response_class=HTMLResponse)
async def root():
    """
    Serve the landing page. Clients visiting the Render URL see a
    proper product page, not a raw JSON health-check response.
    """
    return FileResponse("static/landing.html")


# ─── ROUTE 1b: HEALTH CHECK (for infra / uptime monitors) ───────────
# WHAT: Separate JSON health endpoint for uptime monitors and CI checks.
# WHY: The root now returns HTML, so automated health checks (Render's
#      own health probe, UptimeRobot, CI pipelines) need a dedicated
#      endpoint that still returns parseable JSON. /health is the
#      conventional path for this.
@app.get("/health")
async def health():
    """
    Structured health check for infrastructure monitoring.
    Returns { status, database, message } as JSON.
    """
    db_ok = True
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
    except Exception as e:
        logger.error(f"Health check DB query failed: {e}")
        db_ok = False

    return {
        "status": "online" if db_ok else "degraded",
        "database": "ok" if db_ok else "unreachable",
        "message": "Lead Qualification Agent is running!",
    }

# ─── ROUTE 2: CHAT ──────────────────────────────────────────────────
@app.post("/chat")
@limiter.limit("20/minute")
async def chat(request: Request, body: ChatRequest):
    """
    Main chat endpoint.
    Receives a message, processes it through agent.py, returns the bot's reply.

    REQUEST BODY:
        { "session_id": "abc123", "message": "Hi, I need a website" }

    RESPONSE:
        { "reply": "Hello! What's your budget?", "session_id": "abc123" }

    RATE LIMIT: 20 requests/minute per IP. Each request can trigger a
    Gemini API call (sometimes two — reply + extraction), so this is the
    endpoint most exposed to cost abuse if left unlimited.
    """
    try:
        bot_reply = await process_message(body.session_id, body.message)

        return {
            "reply": bot_reply,
            "session_id": body.session_id
        }

    except Exception as e:
        # exc_info=True so the full traceback lands in the logs — without
        # it, the only thing recorded was str(e), which for a lot of
        # exception types (e.g. KeyError) doesn't tell you where it happened.
        logger.error(f"Chat endpoint error for session {body.session_id}: {e}", exc_info=True)

        raise HTTPException(
            status_code=500,
            detail="Sorry, something went wrong. Please try again."
        )

# ─── ROUTE 3: WIDGET ──────────────────────────────────────────────────
@app.get("/widget", response_class=HTMLResponse)
async def widget():
    html = open("static/widget.html").read()
    html = html.replace("{{API_BASE}}", os.getenv("API_BASE_URL", ""))
    return HTMLResponse(html)
    """
    Serve the chat widget HTML from the static folder.
    Shortcut so clients can use /widget instead of /static/widget.html.
    """
    #return FileResponse("static/widget.html")

# ─── ROUTE 4: EXPORT LEADS (with authentication) ──────────────────────
# WHAT: Export all leads as a CSV file.
# WHY: Business owner can download leads for their CRM.
#
# SECURITY: This endpoint requires an `X-Export-Token` header.
#           Set EXPORT_TOKEN in your environment variables.
#           Without this, anyone who guesses the URL can download lead data.
@app.get("/leads/export")
@limiter.limit("10/minute")
async def export_leads(
    request: Request,
    x_export_token: str = Header(default="")
):
    """
    Export all leads as a CSV file.
    Requires X-Export-Token header matching the EXPORT_TOKEN environment variable.

    Example:
        curl -H "X-Export-Token: your-secret-token" https://app.com/leads/export
    """
    # ─── Authentication ──────────────────────────────────────────────
    EXPORT_TOKEN = os.getenv("EXPORT_TOKEN", "")
    if not EXPORT_TOKEN:
        raise HTTPException(status_code=503, detail="Export endpoint is not configured.")
    if x_export_token != EXPORT_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized.")

    # ─── Fetch leads ──────────────────────────────────────────────────
    leads = get_all_leads()

    # ─── If no leads, return empty CSV with headers ──────────────────
    if not leads:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "session_id", "language", "need", "budget", "timeline", "contact", "score", "created_at"])
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=leads_export.csv"}
        )

    # ─── Write data to CSV ───────────────────────────────────────────
    output = io.StringIO()
    fieldnames = ["id", "session_id", "language", "need", "budget", "timeline", "contact", "score", "created_at"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(leads)

    # ─── Return as downloadable file ────────────────────────────────
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads_export.csv"}
    )

# ─── RUN ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
