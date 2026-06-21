"""
Tests for scorer.py — the hot/warm/cold scoring logic.

These matter because scoring directly drives business-critical behavior:
a misclassified "hot" lead either spams Slack with junk or — worse —
a real hot lead silently gets dropped to "warm" and never gets the
push to book a call.
"""
from model import LeadProfile
from scorer import score_lead


def make_profile(**overrides) -> LeadProfile:
    defaults = dict(
        language="en", need="Website", budget="", timeline="", contact="a@b.com",
        score="unknown", stage="greeting",
    )
    defaults.update(overrides)
    return LeadProfile(**defaults)


# ─── Gemini-estimate path (preferred) ─────────────────────────────────
def test_hot_when_budget_and_timeline_both_meet_threshold():
    profile = make_profile(budget="$5,000", timeline="2 weeks")
    fields = {"budget_usd_estimate": 5000.0, "timeline_weeks_estimate": 2.0}
    assert score_lead(profile, fields) == "hot"


def test_warm_when_only_budget_meets_threshold():
    # High budget but a slow timeline shouldn't count as hot.
    profile = make_profile(budget="$5,000", timeline="6 months")
    fields = {"budget_usd_estimate": 5000.0, "timeline_weeks_estimate": 26.0}
    assert score_lead(profile, fields) == "warm"


def test_warm_when_only_timeline_meets_threshold():
    # Fast timeline but tiny budget shouldn't count as hot either.
    profile = make_profile(budget="$50", timeline="ASAP")
    fields = {"budget_usd_estimate": 50.0, "timeline_weeks_estimate": 0.0}
    assert score_lead(profile, fields) == "warm"


def test_cold_when_no_budget_and_no_timeline_signal():
    profile = make_profile(budget="", timeline="")
    fields = {"budget_usd_estimate": 0.0, "timeline_weeks_estimate": float("inf")}
    assert score_lead(profile, fields) == "cold"


def test_exact_threshold_boundary_counts_as_hot():
    # budget == threshold and timeline == threshold should both count
    # ("≥" / "≤" in the docstring, not "<" / ">").
    profile = make_profile(budget="$1,000", timeline="4 weeks")
    fields = {"budget_usd_estimate": 1000.0, "timeline_weeks_estimate": 4.0}
    assert score_lead(profile, fields) == "hot"


def test_budget_none_estimate_falls_back_to_regex_parse():
    # budget_usd_estimate explicitly None (extraction genuinely unsure)
    # must fall back to the regex parser on lead_profile.budget, not be
    # treated as 0 — 0 means "they said no budget", None means "unknown".
    profile = make_profile(budget="$5,000", timeline="2 weeks")
    fields = {"budget_usd_estimate": None, "timeline_weeks_estimate": 2.0}
    assert score_lead(profile, fields) == "hot"


def test_no_profile_fields_dict_at_all_uses_pure_regex_fallback():
    # Older sessions saved before the Gemini-estimate upgrade won't have
    # profile_fields populated with the *_estimate keys at all.
    profile = make_profile(budget="$2,000", timeline="1 week")
    assert score_lead(profile, profile_fields=None) == "hot"
