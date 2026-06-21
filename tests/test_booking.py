"""
Tests for booking.py — slot parsing and confirmation.

parse_booking_choice in particular is the most failure-prone function in
the project: it's trying to interpret free-text human input ("2pm",
"slot 3", "the first one") against a small numbered list, which is
exactly the kind of thing that silently breaks on inputs nobody tried
by hand.
"""
from booking import parse_booking_choice, confirm_booking
import db


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
def test_confirm_booking_success_saves_to_db():
    result = confirm_booking("session-1", "2", SAMPLE_SLOTS)
    assert result["success"] is True
    assert result["slot"] == "2026-06-23 14:00"
    assert "02:00 PM" in result["message"]

    booked = db.get_booked_slots()
    assert "2026-06-23 14:00" in booked


def test_confirm_booking_unparsed_input_does_not_touch_db():
    result = confirm_booking("session-2", "I dunno, surprise me", SAMPLE_SLOTS)
    assert result["success"] is False
    assert result["reason"] == "unparsed"
    assert db.get_booked_slots() == set()


def test_confirm_booking_race_condition_reports_taken():
    # Simulate two leads being offered the same slot: the first booking
    # succeeds, the second hits the UNIQUE constraint and must report
    # "taken" instead of silently double-booking or crashing.
    first = confirm_booking("session-a", "1", SAMPLE_SLOTS)
    assert first["success"] is True

    second = confirm_booking("session-b", "1", SAMPLE_SLOTS)
    assert second["success"] is False
    assert second["reason"] == "taken"
    assert "someone else" in second["message"].lower()

    # Only one booking should exist for that slot.
    booked = db.get_booked_slots()
    assert "2026-06-23 10:00" in booked
