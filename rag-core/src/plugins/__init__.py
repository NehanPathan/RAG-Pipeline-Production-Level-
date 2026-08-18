"""Plugin infrastructure — everything replaceable, nothing hardcoded.

The problem this solves: the project already had four separate hand-rolled
"registries" (`llm/registry.py`, `ingestion/embedders/registry.py`,
`ingestion/ocr/registry.py`, `retrieval/rerankers/registry.py`), each an
if/elif chain that had to be edited to add an implementation, and each with
subtly different error behaviour. Adding a fifth for tools would have made
five.

`PluginRegistry` is one generic implementation of that pattern:

    embedders = PluginRegistry[EmbeddingProvider]("embedder")

    @embedders.register("bge_m3")
    def _build_bge(settings): ...

    provider = embedders.create("bge_m3", settings=settings)

Registration is a decorator, so an implementation declares itself rather
than being enumerated somewhere else — which is what makes a plugin
directory work: drop a module in, import it once, and it is available.

See `src/plugins/discovery.py` for loading a directory of plugin modules.
"""

from src.plugins.registry import PluginNotFoundError, PluginRegistry

__all__ = ["PluginNotFoundError", "PluginRegistry"]
