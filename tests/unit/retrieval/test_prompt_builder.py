from src.domain.value_objects.context_bundle import AssembledContext
from src.retrieval.answer.prompt_builder import PromptBuilder


def test_build_includes_query_and_context():
    context = AssembledContext(formatted_text="[1] some context", total_tokens=10)
    builder = PromptBuilder()

    prompt = builder.build("What is the refund policy?", context)

    assert "What is the refund policy?" in prompt
    assert "[1] some context" in prompt


def test_build_includes_system_instructions_about_citing():
    context = AssembledContext(formatted_text="ctx", total_tokens=5)
    builder = PromptBuilder()

    prompt = builder.build("query", context)

    assert "Cite sources" in prompt
    assert "ONLY the provided context" in prompt
