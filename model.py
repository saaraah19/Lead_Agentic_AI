from typing import Optional
from pydantic import BaseModel, field_validator
from config import SUPPORTED_LANGUAGES, STAGES, SCORE_LABELS


class LeadProfile(BaseModel):
    language: str  = ""        # type is str, validation happens below
    need: str = ""
    budget: str = ""       # keep as str — LLM might say "$5,000+"
    timeline: str = ""     # keep as str — "2 weeks", "asap", etc.
    contact: str = ""

    score: str = "unknown"   # "hot", "warm", "cold", or "unknown" / gets set by scorer.py
    stage: str = "greeting"  # matches STAGES from config.py

    # ───Language Field Validator ──────────
    @field_validator('language')
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v and v not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Language must be one of {SUPPORTED_LANGUAGES}, got '{v}'")
        return v

    # ─── Stage Field Validator ─────────────────
    @field_validator('stage')
    @classmethod
    def validate_stage(cls, v: str) -> str:
        if v not in STAGES:
            raise ValueError(f"Stage must be one of {STAGES}, got '{v}'")
        return v

    @field_validator('score')
    @classmethod
    def validate_score(cls, v: str) -> str:
        allowed = SCORE_LABELS + ["unknown"]
        if v not in allowed:
            raise ValueError(f"Score must be one of {allowed}, got '{v}'")
        return v


# ─── EXTRACTION SCHEMA (Gemini structured output) ─────────────────────
# WHAT: A deliberately separate, permissive schema used ONLY for asking
#       Gemini to read the conversation and pull out lead fields.
# WHY:  LeadProfile enforces business rules (stage must be a real stage,
#       language must be supported, etc.) that don't make sense to hand
#       to an extraction prompt — and mid-conversation, most fields are
#       legitimately still blank, which LeadProfile's validators don't
#       need to (and shouldn't) reject.
#
#       The *_estimate fields are the actual fix for "50 grand" / "500
#       pounds" / "a few hundred": instead of a regex trying to parse
#       human-written numbers, we ask the model — which already
#       understands "fifty thousand", "نصف مليون", "cinq cents euros" —
#       to just hand back a normalized float. Optional[float] (not a
#       plain float defaulting to 0) matters here: 0 means "they said
#       ASAP / no budget", None means "we genuinely don't know yet",
#       and those two cases must never be conflated in scoring.
class ExtractedLeadFields(BaseModel):
    need: str = ""
    budget: str = ""                                  # human-readable, e.g. "$5,000", "50,000 DZD"
    budget_usd_estimate: Optional[float] = None        # normalized number for scoring; None = unknown
    timeline: str = ""                                 # human-readable, e.g. "2 weeks", "ASAP"
    timeline_weeks_estimate: Optional[float] = None     # normalized number for scoring; None = unknown
    contact: str = ""