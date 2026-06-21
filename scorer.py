# ─── IMPORTS ──────────────────────────────────────────────────────────
# WHAT: re (regex) helps us extract numbers from messy text strings.
# WHY: Kept ONLY as a fallback now — see _parse_budget/_parse_timeline below.
import re
import math
from typing import Optional

# WHAT: Import our Pydantic model and the thresholds from config.
# WHY: We need the profile fields and the scoring rules.
from model import LeadProfile
from config import HOT_BUDGET_THRESHOLD, HOT_TIMELINE_WEEKS

# ─── HELPER: PARSE BUDGET STRING → NUMBER (fallback only) ────────────
def _parse_budget(budget_str: str) -> float:
    """
    WHAT: Convert a messy budget string into a clean number using regex.
    WHY:  This is now a FALLBACK, used only when Gemini's structured
          budget_usd_estimate wasn't available (e.g. the extraction call
          failed, or this lead profile predates the upgrade). It has the
          same blind spots regex always has ("50 grand", "500 pounds",
          "a few hundred" will be parsed wrong or missed) — score_lead()
          prefers the Gemini estimate whenever one exists.

    RULES:
        - Remove everything that's NOT a digit, comma, or dot.
        - Extract the first number we find (e.g., "$5,000" → 5000).
        - If no number is found, return 0 (assume no budget).
        - If "k" is present (e.g., "5k"), multiply by 1000.
    """
    if not budget_str or budget_str.strip() == "":
        return 0.0

    multiplier = 1
    if 'k' in budget_str.lower():
        multiplier = 1000
        budget_str = budget_str.lower().replace('k', '').strip()

    match = re.search(r'([\d,]+\.?[\d]*)', budget_str)
    if not match:
        return 0.0

    num_str = match.group(1).replace(',', '')
    try:
        return float(num_str) * multiplier
    except ValueError:
        return 0.0

# ─── HELPER: PARSE TIMELINE STRING → WEEKS (fallback only) ────────────
def _parse_timeline(timeline_str: str) -> float:
    """
    WHAT: Convert a messy timeline string into a number of weeks using regex.
    WHY:  Fallback only — see _parse_budget docstring above. score_lead()
          prefers Gemini's timeline_weeks_estimate whenever one exists.
    """
    if not timeline_str or timeline_str.strip() == "":
        return float('inf')   # No timeline = no rush

    if any(re.search(rf'\b{word}\b', timeline_str.lower()) for word in ['asap', 'immediate', 'now', 'today']):
        return 0.0

    match = re.search(r'(\d+)', timeline_str)
    if not match:
        text = timeline_str.lower()
        if 'month' in text:
            return 4.0
        elif 'soon' in text:
            return 1.0
        elif 'year' in text:
            return 52.0
        else:
            return float('inf')

    number = float(match.group(1))
    text = timeline_str.lower()

    if 'week' in text:
        weeks = number
    elif 'day' in text:
        weeks = number / 7.0
    elif 'month' in text:
        weeks = number * 4.0
    elif 'year' in text:
        weeks = number * 52.0
    else:
        weeks = number

    return math.ceil(weeks)

# ─── MAIN FUNCTION: SCORE THE LEAD ──────────────────────────────────
def score_lead(lead_profile: LeadProfile, profile_fields: Optional[dict] = None) -> str:
    """
    WHAT: The main entry point — takes a LeadProfile and returns "hot"/"warm"/"cold".
    WHY: Called by agent.py once all critical fields are filled.

    ARGS:
        - lead_profile: the merged profile (need/budget/timeline/contact as text).
        - profile_fields: the session's cached dict, which — once the Gemini
          extraction upgrade in generator.py is wired in — carries
          'budget_usd_estimate' and 'timeline_weeks_estimate' as normalized
          floats straight from the model. These are preferred over the
          regex fallback because they correctly handle "50 grand",
          "500 pounds", "a few hundred", and non-English phrasing that the
          regex parser can't.

    THE RULE (from config.py):
        - HOT if: budget ≥ HOT_BUDGET_THRESHOLD AND timeline ≤ HOT_TIMELINE_WEEKS
        - WARM if: budget > 0 OR timeline < infinity (some interest)
        - COLD if: no budget AND no timeline (just browsing)

    RETURNS: One of "hot", "warm", or "cold".
    """
    profile_fields = profile_fields or {}

    # ─── Step 1: Prefer Gemini's numeric estimates; fall back to regex ──
    budget = profile_fields.get("budget_usd_estimate")
    if budget is None:
        budget = _parse_budget(lead_profile.budget)

    timeline_weeks = profile_fields.get("timeline_weeks_estimate")
    if timeline_weeks is None:
        timeline_weeks = _parse_timeline(lead_profile.timeline)

    # ─── Step 2: Apply the scoring rules ──────────────────────────────
    if budget >= HOT_BUDGET_THRESHOLD and timeline_weeks <= HOT_TIMELINE_WEEKS:
        return "hot"

    if budget > 0 or timeline_weeks < float('inf'):
        return "warm"

    return "cold"