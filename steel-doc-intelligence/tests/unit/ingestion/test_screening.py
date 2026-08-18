"""Document screening, weighted towards what it must let through.

The control is deliberately asymmetric. An irrelevant file sitting in
quarantine costs a steward a moment; a legitimate drawing turned away costs a
user their work and gives them nothing to argue with. So the tests that matter
most here are the ones asserting that real engineering documents -- including
short ones, sparse ones and drawings with almost no prose -- are indexed.
"""

from __future__ import annotations

import pytest

from src.ingestion.screening import DEFAULT_MIN_RELEVANCE, screen_document

SPEC = (
    "The structural steel fabrication and erection shall conform to this "
    "specification. All beams and columns shall be grade ASTM A36. Bolts shall "
    "be M20 grade 8.8 unless the connection detail states otherwise. Welding "
    "shall follow the approved weld procedure. Refer to the drawing schedule "
    "for member sizes and the foundation plan for anchor locations. "
) * 6

FILLER = (
    "Preheat the oven to one hundred and eighty degrees. Cream the butter and "
    "sugar until light and fluffy, then fold in the flour a spoonful at a time. "
) * 12


class TestRealDocumentsAreIndexed:
    def test_a_specification_passes(self):
        verdict = screen_document(SPEC, content_kind="prose", entity_count=2)
        assert verdict.allowed
        assert verdict.relevance > DEFAULT_MIN_RELEVANCE

    @pytest.mark.parametrize("kind", ["cad_native", "vector_drawing", "scanned_drawing", "mixed"])
    def test_anything_the_parser_read_as_a_drawing_passes(self, kind):
        """A detail sheet can carry fewer than fifty words. What the parser
        decided the file *is* outranks any word count."""
        verdict = screen_document("BENT PLATE 5x5x 12 GA.", content_kind=kind)
        assert verdict.allowed
        assert verdict.relevance == 1.0

    def test_a_short_document_is_not_judged_on_relevance(self):
        """A transmittal or covering note is a handful of words, and a ratio
        over eighty tokens is noise. "Cannot tell" must resolve to allow."""
        verdict = screen_document("Sample text for testing." * 20, content_kind="prose")
        assert verdict.allowed
        assert "too short" in verdict.reason

    def test_a_sparse_but_real_document_passes_on_its_entities(self):
        """Vocabulary alone would score this low; an exact designation is not
        a word that turns up by accident."""
        body = ("Please see attached correspondence regarding the item. " * 30) + " ISMB 300"
        verdict = screen_document(body, content_kind="prose", entity_count=3)
        assert verdict.allowed


class TestOutOfScopeIsQuarantined:
    def test_an_unrelated_document_is_quarantined(self):
        verdict = screen_document(FILLER, content_kind="prose")
        assert verdict.quarantined
        assert verdict.category == "out_of_scope"
        assert "no engineering content" in verdict.reason

    def test_the_reason_carries_the_evidence(self):
        """A steward has to be able to disagree with the decision, which means
        seeing what it was based on."""
        verdict = screen_document(FILLER, content_kind="prose")
        assert verdict.signals["words"] > 0
        assert verdict.signals["domain_hits"] == 0

    def test_the_threshold_is_configurable(self):
        strict = screen_document(SPEC, content_kind="prose", min_relevance=1.1)
        assert strict.quarantined


class TestExplicitContent:
    def test_explicit_material_is_quarantined_at_any_length(self):
        """Unlike relevance, this applies however short the file is."""
        verdict = screen_document("sexually explicit", content_kind="prose")
        assert verdict.quarantined
        assert verdict.category == "explicit"

    def test_it_beats_a_drawing_classification(self):
        """A file the parser called a drawing is still refused if its text is
        explicit -- the relevance shortcut must not become a bypass."""
        verdict = screen_document("pornography", content_kind="cad_native")
        assert verdict.quarantined

    def test_the_reason_never_repeats_the_offending_text(self):
        """An audit record that quotes the match has republished it."""
        verdict = screen_document("hardcore sex " + FILLER, content_kind="prose")
        assert verdict.quarantined
        assert "hardcore" not in verdict.reason.lower()

    @pytest.mark.parametrize(
        "body",
        [
            "The contractor was bloody late and the detail is a mess.",
            "This is a damn stupid way to detail a moment connection.",
        ],
    )
    def test_ordinary_site_language_is_not_screened(self, body):
        """This is a scope control, not a profanity filter. Site
        correspondence swears, and that is not a reason to quarantine an
        RFI."""
        assert screen_document(body + SPEC, content_kind="prose").allowed
