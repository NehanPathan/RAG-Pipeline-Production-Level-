from __future__ import annotations

from typing import Literal

from src.config import Settings
from src.llm.providers.base import LLMProvider
from src.llm.providers.openai_provider import OpenAIProvider

LLMRole = Literal["small", "large"]


def get_llm_provider(settings: Settings, role: LLMRole) -> LLMProvider:
    """Select and construct the configured LLMProvider for a given role.

    `role="small"` is used for query rewriting/expansion/classification and
    context compression; `role="large"` is used for final answer generation.
    Only "openai" is implemented today — adding another provider means
    adding one branch here, with no call-site changes anywhere else.
    """
    provider_name = settings.small_llm_provider if role == "small" else settings.large_llm_provider
    model = settings.openai_small_model if role == "small" else settings.openai_large_model

    if provider_name == "openai":
        return OpenAIProvider(api_key=settings.openai_api_key, model=model)

    raise ValueError(f"Unsupported LLM provider {provider_name!r} for role {role!r} (only 'openai' is implemented)")
