import pytest

from aiarch.config import Settings
from aiarch.services.guardrails import OutputGuard, luhn_ok, mask_pii, verhoeff_ok


def _aadhaar(prefix: str = "23456789012") -> str:
    """Build a checksum-valid 12-digit number from an 11-digit prefix (test data, not a real ID)."""
    number = next(prefix + str(d) for d in range(10) if verhoeff_ok(prefix + str(d)))
    return f"{number[:4]} {number[4:8]} {number[8:]}"


def test_checksums():
    assert verhoeff_ok("2363") and not verhoeff_ok("2364")          # textbook Verhoeff example
    assert luhn_ok("4111 1111 1111 1111") and not luhn_ok("4111 1111 1111 1112")
    assert not luhn_ok("") and not verhoeff_ok("")


@pytest.mark.parametrize("text", [
    "Invoice 1234 5678 9012 was paid on time.",          # starts with 1: not an Aadhaar shape
    "Order reference 123456789012 shipped.",             # 12 plain digits, not a + number or a mobile
    "PO number 4111 1111 1111 1112 is open.",            # card shape, fails Luhn
    "Ticket 20261123004512 raised.",                     # 14 digits, fails Luhn
    "Claims above INR 500 need receipts within 30 days.",
])
def test_business_numbers_are_not_masked(text):
    assert mask_pii(text) == (text, {})


def test_valid_aadhaar_is_masked_and_invalid_is_not():
    valid = _aadhaar()
    assert mask_pii(f"My Aadhaar is {valid}.") == ("My Aadhaar is <IN_AADHAAR>.", {"IN_AADHAAR": 1})
    bad = valid[:-1] + str((int(valid[-1]) + 1) % 10)
    assert mask_pii(f"Number {bad}.")[1].get("IN_AADHAAR") is None


def test_valid_card_is_masked_whole_not_as_aadhaar():
    masked, found = mask_pii("Card 4111 1111 1111 1111 expires soon, also 4111-1111-1111-1111.")
    assert masked == "Card <CARD> expires soon, also <CARD>." and found == {"CARD": 2}


def test_phone_forms():
    masked, found = mask_pii("Call +91 9876543210 or 9876543210 or +44 2079460958.")
    assert masked == "Call <PHONE> or <PHONE> or <PHONE>." and found == {"PHONE": 3}
    assert mask_pii("Extension 4357, asset 1234567890.")[1] == {}


SOURCE = "Report lost devices to the IT service desk on 9876543210 or it-help@acme.example."


def test_output_guard_masks_source_values_by_default():
    out = OutputGuard()("Call the service desk on 9876543210 [S1].", n_sources=1, sources=[SOURCE])
    assert out.text == "Call the service desk on <PHONE> [S1]." and out.findings == {"PHONE": 1}
    assert Settings().output_keep_source_pii is False


def test_output_guard_can_keep_values_that_come_from_the_sources():
    guard = OutputGuard(keep_source_values=True)
    out = guard("Call 98765-43210 or mail it-help@acme.example, not 9123456780 [S1].", n_sources=1, sources=[SOURCE])
    assert out.text == "Call 98765-43210 or mail it-help@acme.example, not <PHONE> [S1]."
    assert out.findings == {"PHONE": 1}
