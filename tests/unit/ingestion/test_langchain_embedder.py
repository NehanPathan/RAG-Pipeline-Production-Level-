from src.ingestion.embedders.langchain_embedder import LangChainEmbeddingProvider


class _FakeEmbeddings:
    """Records what it was asked to embed, so prefix handling is observable."""

    def __init__(self) -> None:
        self.documents_seen: list[str] = []
        self.queries_seen: list[str] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.documents_seen.extend(texts)
        return [[float(len(t))] for t in texts]

    async def aembed_query(self, text: str) -> list[float]:
        self.queries_seen.append(text)
        return [float(len(text))]


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, list[float]] = {}
        self.set_calls = 0

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value, ttl: int) -> None:
        self.store[key] = value
        self.set_calls += 1


def _provider(embeddings=None, cache=None, **kwargs) -> LangChainEmbeddingProvider:
    return LangChainEmbeddingProvider(
        embeddings or _FakeEmbeddings(),  # type: ignore[arg-type]
        model_id="test-embed",
        dimensions=8,
        cache=cache,
        **kwargs,
    )


class TestEmbedding:
    async def test_embed_texts_returns_one_vector_per_input(self):
        result = await _provider().embed_texts(["a", "bb", "ccc"])

        assert result == [[1.0], [2.0], [3.0]]

    async def test_empty_input_does_not_call_the_model(self):
        embeddings = _FakeEmbeddings()

        assert await _provider(embeddings).embed_texts([]) == []
        assert embeddings.documents_seen == []

    async def test_model_id_and_dimensions_are_reported(self):
        provider = _provider()

        assert provider.model_id == "test-embed"
        assert provider.dimensions == 8


class TestPrefixes:
    """E5 models are trained with an instruction-prefix convention, and the
    query and document sides take *different* prefixes. Applying the wrong one
    degrades retrieval silently."""

    async def test_document_prefix_is_applied_to_texts(self):
        embeddings = _FakeEmbeddings()

        await _provider(embeddings, document_prefix="passage: ").embed_texts(["beam"])

        assert embeddings.documents_seen == ["passage: beam"]

    async def test_query_prefix_is_applied_to_queries(self):
        embeddings = _FakeEmbeddings()

        await _provider(embeddings, query_prefix="query: ").embed_query("beam")

        assert embeddings.queries_seen == ["query: beam"]

    async def test_no_prefix_by_default(self):
        embeddings = _FakeEmbeddings()

        await _provider(embeddings).embed_texts(["beam"])

        assert embeddings.documents_seen == ["beam"]


class TestCaching:
    async def test_second_call_is_served_from_cache(self):
        embeddings = _FakeEmbeddings()
        cache = _FakeCache()
        provider = _provider(embeddings, cache)

        await provider.embed_texts(["ISMB 300"])
        await provider.embed_texts(["ISMB 300"])

        assert embeddings.documents_seen == ["ISMB 300"], "the model must be called only once"

    async def test_partial_hits_only_embed_the_misses_and_keep_order(self):
        embeddings = _FakeEmbeddings()
        cache = _FakeCache()
        provider = _provider(embeddings, cache)
        await provider.embed_texts(["bb"])
        embeddings.documents_seen.clear()

        result = await provider.embed_texts(["a", "bb", "ccc"])

        assert embeddings.documents_seen == ["a", "ccc"]
        assert result == [[1.0], [2.0], [3.0]], "cached and fresh vectors must stay in input order"

    async def test_cache_key_is_scoped_to_the_model(self):
        """Two models produce different vectors for the same string. A shared
        key would serve one model's vectors to the other, into a vector store
        whose whole premise is that its vectors are comparable."""
        cache = _FakeCache()
        first = _provider(cache=cache)
        second = LangChainEmbeddingProvider(
            _FakeEmbeddings(),  # type: ignore[arg-type]
            model_id="other-embed",
            dimensions=8,
            cache=cache,
        )

        await first.embed_texts(["ISMB 300"])
        await second.embed_texts(["ISMB 300"])

        assert len(cache.store) == 2

    async def test_works_with_no_cache_configured(self):
        result = await _provider(cache=None).embed_texts(["a"])

        assert result == [[1.0]]
