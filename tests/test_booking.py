"""
Tests for booking.py — slot parsing and confirmation.

parse_booking_choice is the most failure-prone function in the project:
it tries to interpret free-text human input ("2pm", "slot 3", "the first
one") against a small numbered list — exactly the kind of thing that
silently breaks on inputs nobody tried by hand.

confirm_booking tests use mocks for the Google Calendar layer so the
suite runs without credentials and stays fast.
"""
from unittest.mock import patch
from booking import parse_booking_choice, confirm_booking


SAMPLE_SLOTS = [
    {"slot": "2026-06-23 10:00", "display": "Tue 10:00 AM", "index": 1},
    {"slot": "2026-06-23 14:00", "display": "Tue 02:00 PM", "index": 2},
    {"slot": "2026-06-24 10:00", "display": "Wed 10:00 AM", "index": 3},
]


# ─── parse_booking_choice ──────────────────────────────────────────────

def test_parses_plain_number():
    assert parse_booking_choice("2", SAMPLE_SLOTS) == "2026-06-23 14:00"


def test_parses_number_embedded_in_sentence():
    assert parse_booking_choice("I'll take slot 3 please", SAMPLE_SLOTS) == "2026-06-24 10:00"


def test_parses_time_of_day():
    assert parse_booking_choice("2pm works for me", SAMPLE_SLOTS) == "2026-06-23 14:00"


def test_parses_day_name():
    assert parse_booking_choice("let's do wednesday", SAMPLE_SLOTS) == "2026-06-24 10:00"


def test_parses_first_and_last():
    assert parse_booking_choice("the first one", SAMPLE_SLOTS) == "2026-06-23 10:00"
    assert parse_booking_choice("give me the last slot", SAMPLE_SLOTS) == "2026-06-24 10:00"


def test_returns_none_for_unparseable_input():
    assert parse_booking_choice("hmm not sure yet", SAMPLE_SLOTS) is None


def test_returns_none_for_empty_slot_list():
    assert parse_booking_choice("2", []) is None


# ─── confirm_booking ────────────────────────────────────────────────────
# All Google Calendar calls are mocked so these tests run without
# credentials and don't hit the network.

@patch("booking.save_booking_audit")
@patch("booking.create_calendar_event", return_value="https://calendar.google.com/event/1")
@patch("booking._slot_is_busy", return_value=False)
@patch("booking._get_busy_times", return_value=set())
@patch("booking.generate_available_slots", return_value=SAMPLE_SLOTS)
def test_confirm_booking_success(mock_slots, mock_busy, mock_is_busy, mock_event, mock_audit):
    result = confirm_booking(slot_index=2, lead_email="test@example.com", language="en")
    assert result["status"] == "success"
    assert "02:00 PM" in result["message"]
    assert result["slot"] == "2026-06-23 14:00"
    mock_event.assert_called_once()


@patch("booking.generate_available_slots", return_value=SAMPLE_SLOTS)
def test_confirm_booking_invalid_index_returns_error(mock_slots):
    result = confirm_booking(slot_index=99, lead_email="test@example.com", language="en")
    assert result["status"] == "error"


@patch("booking.save_booking_audit")
@patch("booking.create_calendar_event")
@patch("booking._slot_is_busy", return_value=True)
@patch("booking._get_busy_times", return_value={("2026-06-23T09:00:00", "2026-06-23T11:00:00")})
@patch("booking.generate_available_slots", return_value=SAMPLE_SLOTS)
def test_confirm_booking_slot_taken_blocks_event_creation(mock_slots, mock_busy, mock_is_busy, mock_event, mock_audit):
    # If the slot is busy, the calendar event must never be created.
    result = confirm_booking(slot_index=1, lead_email="test@example.com", language="en")
    assert result["status"] == "error"
    mock_event.assert_not_called()
