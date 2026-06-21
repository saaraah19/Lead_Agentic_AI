"""
Tests for agent.py's stage machine: _get_next_stage and _is_complete.

These matter because a bug here means the bot gets stuck re-asking a
question it already has the answer to, or skips a question it actually
needs — both directly visible to a real lead mid-conversation, and the
exact class of bug the FIX 1/2/3 comments in agent.py show were already
found and patched once.
"""
from agent import _get_next_stage, _is_complete
from model import LeadProfile


def make_profile(**overrides) -> LeadProfile:
    defaults = dict(
        language="en", need="", budget="", timeline="", contact="",
        score="unknown", stage="greeting",
    )
    defaults.update(overrides)
    return LeadProfile(**defaults)


# ─── _get_next_stage ───────────────────────────────────────────────────
def test_greeting_with_nothing_filled_stays_on_greeting():
    profile = make_profile()
    assert _get_next_stage("greeting", profile) == "greeting"


def test_greeting_to_budget_when_only_need_is_known():
    profile = make_profile(need="Website")
    assert _get_next_stage("greeting", profile) == "budget"


def test_greeting_to_timeline_when_need_and_budget_known():
    profile = make_profile(need="Website", budget="$5,000")
    assert _get_next_stage("greeting", profile) == "timeline"


def test_greeting_skips_straight_to_contact_when_user_gives_everything_at_once():
    # A user who says "I need a website, $5k budget, need it in 2 weeks"
    # in one message should skip straight to asking for contact — not
    # walk through budget/timeline stages it already has answers for.
    profile = make_profile(need="Website", budget="$5,000", timeline="2 weeks")
    assert _get_next_stage("greeting", profile) == "contact"


def test_budget_stage_advances_to_timeline_once_budget_filled():
    profile = make_profile(need="Website", budget="$5,000")
    assert _get_next_stage("budget", profile) == "timeline"


def test_budget_stage_holds_if_budget_still_empty():
    profile = make_profile(need="Website")
    assert _get_next_stage("budget", profile) == "budget"


def test_contact_stage_advances_to_scoring_once_contact_filled():
    profile = make_profile(need="Website", budget="$5,000", timeline="2 weeks", contact="a@b.com")
    assert _get_next_stage("contact", profile) == "scoring"


def test_unknown_current_stage_is_returned_unchanged():
    # Defensive: scoring/booking/closed aren't in stage_to_field, so the
    # function should hand the stage back unchanged rather than error.
    profile = make_profile()
    assert _get_next_stage("scoring", profile) == "scoring"
    assert _get_next_stage("booking", profile) == "booking"


# ─── _is_complete ──────────────────────────────────────────────────────
def test_is_complete_true_when_all_critical_fields_present():
    profile = make_profile(
        language="en", need="Website", budget="$5,000",
        timeline="2 weeks", contact="a@b.com",
    )
    assert _is_complete(profile) is True


def test_is_complete_false_when_one_field_missing():
    profile = make_profile(
        language="en", need="Website", budget="$5,000",
        timeline="2 weeks", contact="",
    )
    assert _is_complete(profile) is False


def test_is_complete_false_when_field_is_only_whitespace():
    # A field that's "   " rather than "" should not count as filled —
    # otherwise a stray space in extraction output would silently mark
    # an incomplete lead as complete and score it prematurely.
    profile = make_profile(
        language="en", need="Website", budget="$5,000",
        timeline="2 weeks", contact="   ",
    )
    assert _is_complete(profile) is False
