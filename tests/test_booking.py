# tests/test_booking.py
"""
Tests for booking.py — slot parsing and confirmation.
Updated to match the new confirm_booking signature.
"""

import pytest
from booking import confirm_booking
import db
from unittest.mock import patch

# Sample slots for testing
SAMPLE_SLOTS = [
    {"slot": "2026-07-02T10:00:00+01:00", "display": "Thu 10:00 AM", "index": 1},
    {"slot": "2026-07-02T14:00:00+01:00", "display": "Thu 02:00 PM", "index": 2},
    {"slot": "2026-07-03T10:00:00+01:00", "display": "Fri 10:00 AM", "index": 3},
]


@patch("booking.generate_available_slots")
def test_confirm_booking_success(mock_generate):
    """Test successful booking with a valid slot index."""
    mock_generate.return_value = SAMPLE_SLOTS

    result = confirm_booking(
        slot_index=2,
        lead_email="test@example.com",
        lead_name="Test Lead",
        lead_need="Website",
        language="en"
    )

    assert result["status"] == "success"
    assert "Thu 02:00 PM" in result["message"]
    assert result["event_url"] is not None


@patch("booking.generate_available_slots")
def test_confirm_booking_invalid_slot(mock_generate):
    """Test booking with an invalid slot index."""
    mock_generate.return_value = SAMPLE_SLOTS

    result = confirm_booking(
        slot_index=99,
        lead_email="test@example.com",
        lead_name="Test Lead",
        lead_need="Website",
        language="en"
    )

    assert result["status"] == "error"
    assert "available" in result["message"].lower()


@patch("booking.generate_available_slots")
def test_confirm_booking_taken_slot(mock_generate, monkeypatch):
    """Test booking a slot that's already taken."""
    mock_generate.return_value = SAMPLE_SLOTS

    # First booking succeeds
    result1 = confirm_booking(
        slot_index=1,
        lead_email="test1@example.com",
        lead_name="Test 1",
        lead_need="Website",
        language="en"
    )
    assert result1["status"] == "success"

    # Second booking for the same slot should fail
    # We need to mock _get_busy_times to return the slot as busy
    import calendar_utils
    def mock_busy_times(*args, **kwargs):
        return {("2026-07-02T10:00:00+01:00", "2026-07-02T10:15:00+01:00")}

    monkeypatch.setattr(calendar_utils, "_get_busy_times", mock_busy_times)

    result2 = confirm_booking(
        slot_index=1,
        lead_email="test2@example.com",
        lead_name="Test 2",
        lead_need="Website",
        language="en"
    )

    assert result2["status"] == "error"
    assert "taken" in result2["message"].lower() or "disponible" in result2["message"].lower()