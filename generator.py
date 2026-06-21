# ─── IMPORTS ──────────────────────────────────────────────────────────
# WHAT: Import the new official Gemini SDK (google.genai).
# WHY: This is the modern SDK (v1.0+) that Google actively maintains.
#      The old google.generativeai is being phased out.
from google import genai

# WHAT: Import the types submodule, which holds Content, Part, GenerateContentConfig, etc.
# WHY: We need these to build structured conversation turns and configure the API call.
from google.genai import types

# WHAT: Import our API key and model name from config.py.
# WHY: Centralised config means we can change keys/models in one place.
from config import GEMINI_API_KEY, GEMINI_MODEL, MAX_HISTORY_TURNS

# WHAT: Type hints for optional lists, dicts, and Pydantic models.
# WHY: Makes the code self-documenting and helps IDEs with autocomplete.
from typing import Optional, List, Dict, Type
from pydantic import BaseModel

# WHAT: Our extraction schema (see model.py for why it's separate from LeadProfile).
from model import ExtractedLeadFields

# WHAT: Built-in logging module.
# WHY: We log errors and warnings so we can debug later without crashing the app.
import logging

# ─── CONFIGURE GEMINI CLIENT ─────────────────────────────────────────
# WHAT: Create a single client instance that all calls will reuse.
# WHY: In the new SDK, we don't call genai.configure() globally.
#      Instead, we create a client object that holds the API key.
#      This is cleaner and safer in multi-threaded environments.
client = genai.Client(api_key=GEMINI_API_KEY)

# ─── SET UP LOGGING ──────────────────────────────────────────────────
# WHAT: Create a logger with the name of this file.
# WHY: So logs from generator.py are clearly labelled in the console.
logger = logging.getLogger(__name__)

# ─── HISTORY CAPPING ──────────────────────────────────────────────────
def _cap_history(history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    """
    WHAT: Keep only the most recent MAX_HISTORY_TURNS turns.
    WHY: Both call_gemini() and extract_lead_profile() send the full
         history on every single call. Without a cap, a long-winded lead
         means every subsequent message gets slower and more expensive —
         not because the conversation needs that much context (a
         qualification flow only needs 4 facts), but because we never
         stopped growing the payload. Keeping the most recent turns is
         the right tradeoff here: the facts we're extracting (need,
         budget, timeline, contact) are almost always stated recently,
         and the stage machine in agent.py means we never "go back" to
         something said 20 turns ago.
    """
    if not history:
        return []
    return history[-MAX_HISTORY_TURNS:]

# ─── THE MAIN FUNCTION ────────────────────────────────────────────────
def call_gemini(
    system_prompt: str,           # The bot's personality and rules (e.g., "Reply in Arabic")
    user_message: str,            # The latest text from the visitor
    history: Optional[List[Dict[str, str]]] = None,   # Previous turns [{"role":"user","content":"..."}, ...]
    response_model: Optional[Type[BaseModel]] = None, # Pydantic schema if we want JSON output (optional)
    temperature: float = 0.7,     # 0 = rigid/deterministic, 1 = creative/random
) -> str:
    """
    Send a prompt to Gemini and get back a reply.

    - If response_model is given → returns JSON string matching that schema.
    - If response_model is None → returns plain text.
    """

    # ─── STEP 1: BUILD THE CONVERSATION AS STRUCTURED TURNS ──────────
    # WHAT: We create a list of types.Content objects.
    # WHY: The new SDK expects each turn as a structured object with "role" and "parts".
    #      This is more reliable than gluing everything into one flat string.
    #      Gemini natively understands multi-turn chat this way.
    contents = []

    # ─── 1a. Add conversation history (if it exists) ──────────────────
    # WHAT: Loop through the history list and convert each dict into a types.Content object.
    # WHY: The history contains previous user messages and bot replies.
    #      We need to send them to Gemini so it has full context.
    #      FIX 8: capped to the most recent MAX_HISTORY_TURNS — see
    #      _cap_history() docstring above for why.
    history = _cap_history(history)
    if history:
        for turn in history:
            # WHAT: Extract the role ("user" or "model"/"assistant") and content.
            role = turn.get("role", "user")
            content_text = turn.get("content", "")

            # ─── IMPORTANT: Role normalisation ───────────────────────────
            # WHAT: If the role is "assistant", change it to "model".
            # WHY: The new SDK ONLY accepts "user" and "model" as roles.
            #      "assistant" is NOT a valid role in this SDK and will cause an API error.
            #      We normalise defensively in case the history ever contains "assistant".
            if role == "assistant":
                role = "model"

            # WHAT: Create a Content object with the role and a Part containing the text.
            # WHY: Each turn must be wrapped in types.Content and types.Part.
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part(text=content_text)]
                )
            )

    # ─── 1b. Add the latest user message ──────────────────────────────
    # WHAT: Append the user's new message as the final turn.
    # WHY: This is the new input that Gemini must respond to.
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part(text=user_message)]
        )
    )

    # ─── STEP 2: CONFIGURE THE OUTPUT FORMAT ──────────────────────────
    # WHAT: Build a GenerateContentConfig object that tells Gemini how to respond.
    # WHY: This controls whether Gemini returns plain text or structured JSON,
    #      sets the temperature, and — most importantly — provides the system instruction.
    if response_model:
        # ─── Branch: JSON output (structured) ──────────────────────────
        # WHAT: If a Pydantic model was provided, force Gemini to return JSON
        #       that matches that model's schema.
        # WHY: For future upgrades (e.g., structured extraction of lead fields in one go).
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,        # The bot's rules/personality
            response_mime_type="application/json",   # Force JSON output
            response_schema=response_model,          # Enforce this exact structure
            temperature=temperature,                 # Creativity setting
        )
    else:
        # ─── Branch: Plain text (most common for this project) ─────────
        # WHAT: Return plain text — used for 90% of the conversation turns.
        # WHY: We only need JSON at the very end (if ever), not during the chat.
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,        # The bot's rules/personality
            response_mime_type="text/plain",         # Plain text reply
            temperature=temperature,                 # Creativity setting
        )

    # ─── STEP 3: SEND THE PROMPT AND HANDLE THE RESPONSE ─────────────
    # WHAT: Make the actual API call to Gemini.
    # WHY: This is where the magic happens — all the preparation above
    #      is just to build the request correctly.
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,      # e.g., "gemini-2.0-flash"
            contents=contents,       # The full conversation turns
            config=config,           # The output format + system instruction
        )

        # ─── 3a. Extract the response text ──────────────────────────────
        # WHAT: If Gemini returned text, strip whitespace and return it.
        # WHY: We want a clean string without leading/trailing spaces.
        if response.text:
            return response.text.strip()
        else:
            # WHAT: Gemini returned an empty or blocked response.
            # WHY: This can happen if the content is filtered by safety settings.
            logger.warning("Gemini returned an empty response.")
            return ""

    # ─── 3b. Error handling (fallback) ────────────────────────────────
    # WHAT: Catch ANY exception (network error, API key expired, rate limit, etc.)
    # WHY: We never want the chatbot to crash and show a 500 error to the visitor.
    #      Instead, we log the error and return a friendly fallback message.
    except Exception as e:
        logger.error(f"Gemini call failed: {e}")
        return "I'm having trouble processing that. Could you rephrase?"


# ─── EXTRACTION PROMPT ────────────────────────────────────────────────
# WHAT: System instruction used ONLY for the structured extraction call.
# WHY: Kept as a separate constant (not built inline) so it's easy to
#      tune independently of the conversational system prompt, and easy
#      to unit-test in isolation.
_EXTRACTION_SYSTEM_PROMPT = """
You are extracting structured lead-qualification data from a sales conversation.
Read the ENTIRE conversation below and extract whatever the lead has actually
stated. Do not invent or guess — leave a field blank/null if it was never
mentioned.

Fields:
- need: a short (under 15 words) description of what they're looking for.
- budget: a clean, human-readable string of whatever they said about budget,
  in their own currency (e.g. "$5,000", "50,000 DZD", "half a million euros").
  Empty string if never mentioned.
- budget_usd_estimate: your best-effort numeric conversion to USD as a plain
  number, handling informal phrasing, any currency, and spoken-number formats
  ("50 grand", "a couple hundred bucks", "نصف مليون", "cinq cents euros").
  Use null if a number genuinely cannot be estimated (e.g. "not sure yet",
  "no budget set"). Use 0 only if they explicitly said they have no budget.
- timeline: a clean, human-readable string (e.g. "2 weeks", "ASAP", "next quarter").
  Empty string if never mentioned.
- timeline_weeks_estimate: your best-effort numeric conversion to weeks as a
  plain number. Use 0 for "ASAP" / "immediately" / "today". Use null if
  genuinely unclear (e.g. "no rush", "whenever works").
- contact: an email address or phone number, copied exactly as the user wrote it.
  Empty string if never given.

Respond with the structured fields only — no commentary, no markdown.
""".strip()


# ─── STRUCTURED LEAD EXTRACTION ──────────────────────────────────────
def extract_lead_profile(
    history: List[Dict[str, str]],
) -> Optional[ExtractedLeadFields]:
    """
    Make ONE structured Gemini call over the full conversation history and
    return an ExtractedLeadFields instance (or None on failure).

    WHY THIS REPLACES REGEX EXTRACTION:
        Regex parsing of budget/timeline strings ("50 grand", "500 pounds",
        "a few hundred") is an unbounded pattern-matching problem — every
        fix just uncovers the next phrasing it doesn't handle, in three
        languages. Gemini already understands these phrasings natively;
        asking it for a normalized number is strictly more robust than
        chasing it with regex.

    WHY RETURN None ON FAILURE (not an empty ExtractedLeadFields):
        The caller needs to distinguish "the model ran and confirmed
        nothing is known yet" from "the call failed". Returning empty
        fields on failure would cause the caller to overwrite previously
        known values with blanks — actively losing data the lead already
        gave us. None means "skip this turn, keep what we had."

    NOTE ON BLOCKING:
        This is a synchronous network call, same as call_gemini(). The
        caller (agent.py) is async and must run this via
        asyncio.to_thread() so it doesn't block the event loop for other
        concurrent conversations.
    """
    if not history:
        return None

    contents = []
    for turn in _cap_history(history):
        role = turn.get("role", "user")
        if role == "assistant":
            role = "model"
        contents.append(
            types.Content(role=role, parts=[types.Part(text=turn.get("content", ""))])
        )

    config = types.GenerateContentConfig(
        system_instruction=_EXTRACTION_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=ExtractedLeadFields,
        temperature=0,   # deterministic — this is a read/extraction task, not a creative one
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )

        # The SDK parses response_schema results into `.parsed` automatically
        # when one is provided. Fall back to manual validation of `.text` in
        # case a given SDK version doesn't populate `.parsed` for this call shape.
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, ExtractedLeadFields):
            return parsed
        if response.text:
            return ExtractedLeadFields.model_validate_json(response.text)

        logger.warning("Lead extraction returned an empty response.")
        return None

    except Exception as e:
        logger.error(f"Lead extraction call failed: {e}")
        return None