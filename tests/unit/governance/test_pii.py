from __future__ import annotations

import pytest

from src.governance.pii import PIIRedactor, reset_redactor


@pytest.fixture(autouse=True)
def _reset():
    reset_redactor()
    yield
    reset_redactor()


@pytest.fixture
def redactor() -> PIIRedactor:
    return PIIRedactor()


class TestDetection:
    def test_redacts_email(self, redactor):
        result = redactor.redact("Contact jane.doe@example.com for access.")
        assert "jane.doe@example.com" not in result.text
        assert "[EMAIL]" in result.text
        assert result.counts == {"email": 1}

    def test_redacts_valid_credit_card(self, redactor):
        result = redactor.redact("Card 4111 1111 1111 1111 on file.")
        assert "4111 1111 1111 1111" not in result.text
        assert "CARD" in result.text

    def test_ignores_digit_runs_that_fail_luhn(self, redactor):
        """Order numbers are 16 digits too. Without the Luhn check this
        detector would redact half a business corpus."""
        result = redactor.redact("Order number 1234567812345678 shipped.")
        assert "1234567812345678" in result.text

    def test_redacts_valid_ssn(self, redactor):
        result = redactor.redact("SSN 123-45-6789 on record.")
        assert "123-45-6789" not in result.text

    def test_ignores_structurally_impossible_ssn(self, redactor):
        assert "000-45-6789" in redactor.redact("Ref 000-45-6789").text

    def test_redacts_api_keys(self, redactor):
        text = "Use sk-abcdefghijklmnopqrstuvwxyz123456 to authenticate."
        result = redactor.redact(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result.text
        assert "[SECRET]" in result.text

    def test_redacts_ip_addresses(self, redactor):
        assert "192.168.1.100" not in redactor.redact("Host 192.168.1.100").text

    @pytest.mark.parametrize(
        "text",
        [
            "Call 555-123-4567.",            # end of sentence — the regression
            "Call 555-123-4567 now",
            "Reach me on 555-123-4567, please",
            "Phone: (555) 123-4567.",
            "Call +1 555-123-4567.",
        ],
    )
    def test_redacts_phone_numbers_including_before_a_full_stop(self, redactor, text):
        """A trailing-period guard used to reject every phone number that
        ended a sentence — which is most of them in real prose."""
        assert "123-4567" not in redactor.redact(text).text

    @pytest.mark.parametrize(
        "text",
        [
            "Version 1.2.3.4 released",
            "Ratio 3.14159 exactly",
            "Item 12.50 costs 99.99",
        ],
    )
    def test_phone_detector_ignores_decimals_and_versions(self, redactor, text):
        """The guard that caused the trailing-period bug existed for a reason:
        without it, decimals and dotted version numbers get read as phone
        numbers. Asserted on the phone detector specifically, because
        `1.2.3.4` is *also* a syntactically valid IP address and the IP
        detector legitimately claims it (see the data card -- `ip_address` is
        the detector most worth disabling on identifier-heavy corpora)."""
        assert "phone" not in redactor.redact(text).counts

    def test_decimals_survive_untouched(self, redactor):
        text = "Ratio 3.14159 exactly"
        assert redactor.redact(text).text == text

    def test_leaves_clean_text_untouched(self, redactor):
        text = "The refund window is 30 days from purchase."
        result = redactor.redact(text)
        assert result.text == text
        assert result.redacted is False


class TestMasking:
    def test_card_keeps_a_recognisable_tail(self, redactor):
        """A fully-masked value destroys a human reviewer's ability to tell
        two redactions apart."""
        result = redactor.redact("Card 4111 1111 1111 1111.")
        assert "1111]" in result.text

    def test_multiple_occurrences_are_all_masked(self, redactor):
        result = redactor.redact("a@x.com and b@y.com and c@z.com")
        assert result.counts["email"] == 3
        assert "@x.com" not in result.text

    def test_surrounding_text_is_preserved(self, redactor):
        result = redactor.redact("Email a@b.com now please")
        assert result.text.startswith("Email ")
        assert result.text.endswith(" now please")

    def test_overlapping_matches_keep_the_longest(self, redactor):
        """An email contains substrings other detectors can match; masking
        the shorter one first would leave fragments of the longer exposed."""
        result = redactor.redact("Write to AB123456@example.com today")
        assert "AB123456@example.com" not in result.text
        assert "@example.com" not in result.text


class TestConfiguration:
    def test_detector_subset_is_honoured(self):
        limited = PIIRedactor(["email"])
        assert limited.detector_names == ["email"]
        result = limited.redact("a@b.com and 192.168.1.1")
        assert "a@b.com" not in result.text
        assert "192.168.1.1" in result.text

    def test_unknown_detector_names_are_ignored_not_fatal(self):
        """A stale config naming a removed detector must not disable
        redaction entirely."""
        redactor = PIIRedactor(["email", "does_not_exist"])
        assert redactor.detector_names == ["email"]

    def test_empty_selection_enables_everything(self):
        assert len(PIIRedactor([]).detector_names) > 5

    def test_redact_all_reports_a_total(self, redactor):
        texts, total = redactor.redact_all(["a@b.com", "clean text", "c@d.com"])
        assert total == 2
        assert texts[1] == "clean text"


class TestScanning:
    def test_scan_does_not_modify_text(self, redactor):
        matches = redactor.scan("reach me at a@b.com")
        assert len(matches) == 1
        assert matches[0].detector == "email"

    def test_scan_of_empty_text_is_empty(self, redactor):
        assert redactor.scan("") == []


class TestQuestionRedaction:
    """The question is also the *search key*, so it gets a narrower detector
    set than retrieved context: masking an email someone is legitimately
    searching for would silently break their query."""

    def test_question_redactor_is_narrower_than_the_full_one(self):
        from src.governance.pii import get_question_redactor, get_redactor

        assert set(get_question_redactor().detector_names) < set(get_redactor().detector_names)

    def test_high_harm_values_are_masked_in_questions(self):
        """No search utility, active incident if leaked."""
        from src.governance.pii import get_question_redactor

        r = get_question_redactor().redact(
            "is sk-abcdefghijklmnopqrstuvwxyz123456 still valid?", surface="question"
        )
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in r.text

    def test_credit_cards_are_masked_in_questions(self):
        from src.governance.pii import get_question_redactor

        r = get_question_redactor().redact("refund card 4111 1111 1111 1111", surface="question")
        assert "4111 1111 1111 1111" not in r.text

    def test_searchable_contact_details_survive_in_questions(self):
        """An email in a question is usually the thing being looked for.
        Masking it would turn a working search into a silent miss."""
        from src.governance.pii import get_question_redactor

        text = "what did jane.doe@acme.com agree to?"
        assert get_question_redactor().redact(text, surface="question").text == text
