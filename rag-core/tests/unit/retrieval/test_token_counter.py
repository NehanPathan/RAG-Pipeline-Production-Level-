from src.retrieval.context.token_counter import TokenCounter


def test_count_returns_positive_for_nonempty_text():
    counter = TokenCounter(model="gpt-4o-mini")

    assert counter.count("hello world") > 0


def test_count_empty_string_is_zero():
    counter = TokenCounter(model="gpt-4o-mini")

    assert counter.count("") == 0


def test_count_falls_back_to_default_encoding_for_unknown_model():
    counter = TokenCounter(model="some-totally-unknown-model-name")

    assert counter.count("hello world") > 0


def test_count_longer_text_has_more_tokens():
    counter = TokenCounter(model="gpt-4o-mini")

    short = counter.count("hello")
    long_count = counter.count("hello " * 50)

    assert long_count > short
