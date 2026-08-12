from __future__ import annotations

from typing import Literal

from src.config import Settings, get_settings
from src.governance.policy import get_policy
from src.llm.gateway import LLMGateway, ProviderBinding
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.plugins import PluginRegistry

logger = get_logger(__name__)

LLMRole = Literal["small", "large"]

# One registry replacing the previous if/elif chain. Adding a provider is now
# a decorated function in this file (or any imported module), with no edit to
# the selection logic and no call-site changes anywhere.
providers: PluginRegistry[LLMProvider] = PluginRegistry("llm_provider")


@providers.register("openai", description="OpenAI Chat Completions")
def _build_openai(settings: Settings, role: LLMRole) -> LLMProvider:
    from src.llm.providers.openai_provider import OpenAIProvider

    model = settings.openai_small_model if role == "small" else settings.openai_large_model
    return OpenAIProvider(api_key=settings.openai_api_key, model=model)


@providers.register("anthropic", description="Anthropic Claude Messages API")
def _build_anthropic(settings: Settings, role: LLMRole) -> LLMProvider:
    from src.llm.providers.anthropic_provider import AnthropicProvider

    model = settings.anthropic_small_model if role == "small" else settings.anthropic_large_model
    return AnthropicProvider(api_key=settings.anthropic_api_key, model=model)


@providers.register("ollama", description="Local Ollama via its OpenAI-compatible /v1 endpoint")
def _build_ollama(settings: Settings, role: LLMRole) -> LLMProvider:
    from src.llm.providers.openai_compatible_provider import OpenAICompatibleProvider

    model = settings.ollama_small_model if role == "small" else settings.ollama_large_model
    return OpenAICompatibleProvider(
        api_key="",
        model=model,
        base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
        vendor="ollama",
        # Ollama's compatibility layer rejects the parameter outright.
        supports_usage_in_stream=False,
        timeout=settings.llm_timeout_seconds,
    )


@providers.register("openrouter", description="OpenRouter multi-vendor gateway")
def _build_openrouter(settings: Settings, role: LLMRole) -> LLMProvider:
    from src.llm.providers.openai_compatible_provider import OpenAICompatibleProvider

    model = settings.openrouter_small_model if role == "small" else settings.openrouter_large_model
    return OpenAICompatibleProvider(
        api_key=settings.openrouter_api_key,
        model=model,
        base_url=settings.openrouter_base_url,
        vendor="openrouter",
        timeout=settings.llm_timeout_seconds,
    )


@providers.register("azure", description="Azure OpenAI deployment")
def _build_azure(settings: Settings, role: LLMRole) -> LLMProvider:
    from src.llm.providers.openai_compatible_provider import OpenAICompatibleProvider

    # Azure addresses a *deployment name*, not a model name, and the two are
    # frequently different — a common source of 404s on first setup.
    deployment = (
        settings.azure_openai_small_deployment
        if role == "small"
        else settings.azure_openai_large_deployment
    )
    return OpenAICompatibleProvider(
        api_key=settings.azure_openai_api_key,
        model=deployment,
        base_url=(
            f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/{deployment}"
            f"?api-version={settings.azure_openai_api_version}"
        ),
        vendor="azure",
        timeout=settings.llm_timeout_seconds,
    )


def get_llm_provider(settings: Settings, role: LLMRole) -> LLMProvider:
    """The gateway for a role — every caller's single entry point to an LLM.

    Returns an `LLMGateway`, which implements `LLMProvider`, so the many
    existing call sites that expect a bare provider keep working while
    silently gaining fallback, cost accounting and the approved-model check.
    """
    return build_gateway(settings, role)


def build_gateway(settings: Settings | None = None, role: LLMRole = "small") -> LLMGateway:
    settings = settings or get_settings()
    policy = get_policy()

    configured = settings.small_llm_provider if role == "small" else settings.large_llm_provider
    chain_names = _resolve_chain(configured, settings.llm_fallback_chain)

    bindings: list[ProviderBinding] = []
    for name in chain_names:
        decision = policy.check_llm_provider(name)
        if decision.denied:
            # Skip rather than raise: one unapproved entry in a fallback chain
            # should not deny the whole role its approved providers. An empty
            # resulting chain does raise, below.
            logger.warning(
                "gateway_provider_not_approved",
                provider=name,
                role=role,
                reason=decision.reason,
            )
            continue
        if not providers.has(name):
            logger.warning("gateway_provider_unknown", provider=name, available=providers.names())
            continue
        try:
            bindings.append(
                ProviderBinding(name=name, provider=providers.create(name, settings=settings, role=role))
            )
        except Exception as exc:
            # A missing API key for a *fallback* provider must not stop the
            # primary from being usable.
            logger.warning("gateway_provider_unavailable", provider=name, error=str(exc))

    if not bindings:
        raise ValueError(
            f"No usable LLM provider for role {role!r}. Configured chain: {chain_names}. "
            f"Registered: {providers.names()}. Approved: {sorted(policy.allowed_llm_providers)}."
        )

    gateway = LLMGateway(role=role, chain=bindings, max_retries=settings.llm_max_retries)
    logger.info("gateway_built", **gateway.describe())
    return gateway


def _resolve_chain(primary: str, fallbacks: list[str]) -> list[str]:
    """Primary first, then the configured fallbacks, de-duplicated.

    Order matters and duplicates are silently harmful: a chain of
    ["openai", "openai"] would retry the same failing provider and look like
    a working fallback.
    """
    ordered = [primary.strip().lower()] + [f.strip().lower() for f in fallbacks]
    seen: set[str] = set()
    result: list[str] = []
    for name in ordered:
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result
