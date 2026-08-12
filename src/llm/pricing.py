from __future__ import annotations

from dataclasses import dataclass

from src.monitoring.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens. Prices change; this table is an estimate for
    budgeting and alerting, not an invoice."""

    prompt: float
    completion: float

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.prompt + completion_tokens * self.completion
        ) / 1_000_000


# Keyed by a prefix of the model id so dated snapshots (`gpt-4o-2024-08-06`)
# match their base model without a new entry per release.
_PRICES: dict[str, ModelPrice] = {
    "gpt-4o-mini": ModelPrice(0.15, 0.60),
    "gpt-4o": ModelPrice(2.50, 10.00),
    "gpt-4.1-mini": ModelPrice(0.40, 1.60),
    "gpt-4.1": ModelPrice(2.00, 8.00),
    "o4-mini": ModelPrice(1.10, 4.40),
    "claude-haiku-4-5": ModelPrice(1.00, 5.00),
    "claude-sonnet-4-6": ModelPrice(3.00, 15.00),
    "claude-sonnet-4-5": ModelPrice(3.00, 15.00),
    "claude-opus-4-1": ModelPrice(15.00, 75.00),
    "gemini-1.5-flash": ModelPrice(0.075, 0.30),
    "gemini-1.5-pro": ModelPrice(1.25, 5.00),
    "gemini-2.0-flash": ModelPrice(0.10, 0.40),
    "text-embedding-3-small": ModelPrice(0.02, 0.0),
    "text-embedding-3-large": ModelPrice(0.13, 0.0),
}

# Locally-hosted models cost no API spend. Recorded as zero rather than
# unknown so a self-hosted deployment shows a real 0.00 instead of a gap.
_LOCAL_PREFIXES = ("llama", "mistral", "qwen", "phi", "gemma", "bge", "e5", "intfloat")


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimated spend for one call, or None when the model is unpriced.

    Returns None rather than 0.0 for an unknown model: a silent zero would
    understate spend on exactly the models nobody has reviewed yet, which is
    the opposite of what a cost control should do.
    """
    price = lookup(model)
    if price is None:
        return None
    return price.cost_usd(prompt_tokens, completion_tokens)


def lookup(model: str) -> ModelPrice | None:
    if not model:
        return None
    name = model.strip().lower()
    if any(name.startswith(prefix) for prefix in _LOCAL_PREFIXES):
        return ModelPrice(0.0, 0.0)
    # Longest prefix wins, so "gpt-4o-mini" is not matched by "gpt-4o".
    for prefix in sorted(_PRICES, key=len, reverse=True):
        if name.startswith(prefix):
            return _PRICES[prefix]
    return None


def known_models() -> list[str]:
    return sorted(_PRICES)
