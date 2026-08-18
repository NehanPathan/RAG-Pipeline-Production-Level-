from __future__ import annotations

from typing import Literal

from src.config import Settings, get_settings
from src.governance.policy import get_policy
from src.llm.gateway import LLMGateway, ProviderBinding
from src.llm.providers.base import LLMProvider
from src.llm.providers.langchain_provider import LangChainChatProvider
from src.monitoring.logger import get_logger
from src.plugins import PluginRegistry

logger = get_logger(__name__)

LLMRole = Literal["small", "large"]

# One registry replacing the previous if/elif chain. Adding a provider is now
# a decorated function in this file (or any imported module), with no edit to
# the selection logic and no call-site changes anywhere.
#
# Each factory now returns a LangChain chat model wrapped in
# LangChainChatProvider, rather than a bespoke HTTP client. The registry, the
# approved-provider policy check and the gateway around it are unchanged --
# those carry governance behaviour LangChain has no equivalent for.
providers: PluginRegistry[LLMProvider] = PluginRegistry("llm_provider")


@providers.register("openai", description="OpenAI Chat Completions (langchain-openai)")
def _build_openai(settings: Settings, role: LLMRole) -> LLMProvider:
    from langchain_openai import ChatOpenAI

    model = settings.openai_small_model if role == "small" else settings.openai_large_model
    return LangChainChatProvider(
        ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        ),
        model_id=model,
    )


@providers.register("anthropic", description="Anthropic Claude (langchain-anthropic)")
def _build_anthropic(settings: Settings, role: LLMRole) -> LLMProvider:
    from langchain_anthropic import ChatAnthropic

    model = settings.anthropic_small_model if role == "small" else settings.anthropic_large_model
    return LangChainChatProvider(
        ChatAnthropic(
            model=model,
            api_key=settings.anthropic_api_key or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        ),
        model_id=model,
    )


@providers.register("ollama", description="Local Ollama via its OpenAI-compatible /v1 endpoint")
def _build_ollama(settings: Settings, role: LLMRole) -> LLMProvider:
    from langchain_openai import ChatOpenAI

    model = settings.ollama_small_model if role == "small" else settings.ollama_large_model
    return LangChainChatProvider(
        ChatOpenAI(
            model=model,
            # Ollama ignores the key but the client requires a non-empty one.
            api_key="ollama",
            base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        ),
        model_id=model,
    )


@providers.register("openrouter", description="OpenRouter multi-vendor gateway")
def _build_openrouter(settings: Settings, role: LLMRole) -> LLMProvider:
    from langchain_openai import ChatOpenAI

    model = settings.openrouter_small_model if role == "small" else settings.openrouter_large_model
    return LangChainChatProvider(
        ChatOpenAI(
            model=model,
            api_key=settings.openrouter_api_key or None,
            base_url=settings.openrouter_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        ),
        model_id=model,
    )


@providers.register("azure", description="Azure OpenAI deployment")
def _build_azure(settings: Settings, role: LLMRole) -> LLMProvider:
    from langchain_openai import AzureChatOpenAI

    # Azure addresses a *deployment name*, not a model name, and the two are
    # frequently different — a common source of 404s on first setup.
    deployment = (
        settings.azure_openai_small_deployment
        if role == "small"
        else settings.azure_openai_large_deployment
    )
    return LangChainChatProvider(
        AzureChatOpenAI(
            azure_deployment=deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
            api_key=settings.azure_openai_api_key or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        ),
        model_id=deployment,
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
