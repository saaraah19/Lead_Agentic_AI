# tests/test_booking.py
"""
Tests for booking.py — slot confirmation and free-text slot parsing.

Three issues made the original tests unreliable / excluded from CI:
  1. create_calendar_event was never mocked → real API calls with fake creds
  2. _get_busy_times was never mocked → same
  3. The DB race-condition guard (db.save_booking) wasn't wired up yet

All three are now fixed. Coverage:
  confirm_booking  — success, invalid index, freebusy rejection, DB race guard
  parse_booking_choice — number, am/pm time, day name, ordinal, no-match, empty
"""
import pytest
from unittest.mock import patch
from booking import confirm_booking, parse_booking_choice

# ─── Shared fixtures ──────────────────────────────────────────────────
SAMPLE_SLOTS = [
    {"slot": "2026-07-02T10:00:00+01:00", "display": "Thu 10:00 AM", "index": 1},
    {"slot": "2026-07-02T14:00:00+01:00", "display": "Thu 02:00 PM", "index": 2},
    {"slot": "2026-07-03T10:00:00+01:00", "display": "Fri 10:00 AM", "index": 3},
]
FAKE_EVENT_URL = "https://calendar.google.com/event/fake123"


# ─── confirm_booking ──────────────────────────────────────────────────

@patch("booking.create_calendar_event", return_value=FAKE_EVENT_URL)
@patch("booking._get_busy_times", return_value=set())
@patch("booking.generate_available_slots")
def test_confirm_booking_success(mock_slots, mock_busy, mock_create):
    """Happy path: valid slot, not busy, DB claim succeeds."""
    mock_slots.return_value = SAMPLE_SLOTS

    result = confirm_booking(
        slot_index=2,
        lead_email="test@example.com",
        lead_name="Test Lead",
        lead_need="Website",
        language="en",
        session_id="test-session-001",
    )

    assert result["status"] == "success"
    assert "Thu 02:00 PM" in result["message"]
    assert result["event_url"] == FAKE_EVENT_URL


@patch("booking.create_calendar_event", return_value=FAKE_EVENT_URL)
@patch("booking._get_busy_times", return_value=set())
@patch("booking.generate_available_slots")
def test_confirm_booking_invalid_slot_index(mock_slots, mock_busy, mock_create):
    """Slot index 99 doesn't exist → status error, message mentions availability."""
    mock_slots.return_value = SAMPLE_SLOTS

    result = confirm_booking(
        slot_index=99,
        lead_email="test@example.com",
        language="en",
    )

    assert result["status"] == "error"
    assert "available" in result["message"].lower()


@patch("booking.create_calendar_event", return_value=FAKE_EVENT_URL)
@patch("booking._get_busy_times")
@patch("booking.generate_available_slots")
def test_confirm_booking_slot_rejected_by_freebusy(mock_slots, mock_busy, mock_create):
    """Google Calendar already has an event in this window → error before API call."""
    mock_slots.return_value = SAMPLE_SLOTS
    # Slot 1 (10:00–10:15) is occupied
    mock_busy.return_value = {
        ("2026-07-02T10:00:00+01:00", "2026-07-02T10:15:00+01:00")
    }

    result = confirm_booking(
        slot_index=1,
        lead_email="test@example.com",
        language="en",
    )

    assert result["status"] == "error"
    mock_create.assert_not_called()   # should bail before touching the calendar


@patch("booking.create_calendar_event", return_value=FAKE_EVENT_URL)
@patch("booking._get_busy_times", return_value=set())
@patch("booking.generate_available_slots")
def test_confirm_booking_db_race_guard_blocks_double_booking(mock_slots, mock_busy, mock_create):
    """
    Two requests pass the freebusy check in the same window.
    The UNIQUE constraint on bookings.slot_time must let only one through.
    """
    mock_slots.return_value = SAMPLE_SLOTS

    result1 = confirm_booking(
        slot_index=1,
        lead_email="lead-a@example.com",
        language="en",
        session_id="session-A",
    )
    assert result1["status"] == "success"

    # Same slot, different session — DB constraint should block this.
    result2 = confirm_booking(
        slot_index=1,
        lead_email="lead-b@example.com",
        language="en",
        session_id="session-B",
    )
    assert result2["status"] == "error"


@patch("booking.create_calendar_event", side_effect=Exception("Calendar API down"))
@patch("booking._get_busy_times", return_value=set())
@patch("booking.generate_available_slots")
def test_confirm_booking_calendar_failure_rolls_back_db_claim(mock_slots, mock_busy, mock_create):
    """
    If the calendar API throws after the DB claim succeeds,
    the DB record should be rolled back so the slot can be retried.
    Verified by confirming the same slot succeeds when the API recovers.
    """
    mock_slots.return_value = SAMPLE_SLOTS

    # First attempt fails at calendar step
    result1 = confirm_booking(
        slot_index=1,
        lead_email="lead@example.com",
        language="en",
        session_id="session-retry",
    )
    assert result1["status"] == "error"

    # Slot should now be retryable (DB claim rolled back)
    mock_create.side_effect = None
    mock_create.return_value = FAKE_EVENT_URL

    result2 = confirm_booking(
        slot_index=1,
        lead_email="lead@example.com",
        language="en",
        session_id="session-retry",
    )
    assert result2["status"] == "success"


# ─── parse_booking_choice ─────────────────────────────────────────────

def test_parse_slot_by_number():
    assert parse_booking_choice("2", SAMPLE_SLOTS) == SAMPLE_SLOTS[1]["slot"]


def test_parse_slot_by_time_pm():
    # Regression: "2pm" previously wasn't parsed (regex existed but was never called).
    result = parse_booking_choice("2pm", SAMPLE_SLOTS)
    assert result == SAMPLE_SLOTS[1]["slot"]


def test_parse_slot_by_time_am():
    result = parse_booking_choice("10am", SAMPLE_SLOTS)
    assert result == SAMPLE_SLOTS[0]["slot"]


def test_parse_slot_by_day_name_english():
    result = parse_booking_choice("friday", SAMPLE_SLOTS)
    assert result == SAMPLE_SLOTS[2]["slot"]


def test_parse_slot_first_ordinal():
    result = parse_booking_choice("the first one", SAMPLE_SLOTS)
    assert result == SAMPLE_SLOTS[0]["slot"]


def test_parse_slot_last_ordinal():
    result = parse_booking_choice("the last slot", SAMPLE_SLOTS)
    assert result == SAMPLE_SLOTS[-1]["slot"]


def test_parse_slot_no_match_returns_none():
    assert parse_booking_choice("I have no idea what time", SAMPLE_SLOTS) is None


def test_parse_slot_empty_input_returns_none():
    assert parse_booking_choice("", SAMPLE_SLOTS) is None


def test_parse_slot_empty_slot_list_returns_none():
    assert parse_booking_choice("1", []) is None
