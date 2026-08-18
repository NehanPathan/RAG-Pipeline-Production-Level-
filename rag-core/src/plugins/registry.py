from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from src.monitoring.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class PluginNotFoundError(KeyError):
    """Raised when a name is requested that nothing registered.

    Carries the available names in the message, because the overwhelmingly
    common cause is a typo or a plugin module that was never imported, and
    both are diagnosed instantly from the list.
    """

    def __init__(self, kind: str, name: str, available: list[str]) -> None:
        super().__init__(
            f"No {kind} plugin registered as {name!r}. "
            f"Available: {available or '(none registered — was the plugin module imported?)'}"
        )
        self.kind = kind
        self.name = name
        self.available = available


@dataclass(frozen=True)
class PluginSpec(Generic[T]):
    """One registered implementation plus the metadata governance needs.

    `requires_flag` is what lets a plugin be shipped but switched off: the
    registry refuses to build it unless that feature flag is enabled, so
    disabling a capability is a config change rather than a deploy.
    """

    name: str
    factory: Callable[..., T]
    description: str = ""
    requires_flag: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


class PluginRegistry(Generic[T]):
    """A named collection of interchangeable implementations of one interface.

    Deliberately not a global singleton: each registry is a module-level
    object owned by the package that defines the interface, so `tools` and
    `embedders` cannot collide, and importing one does not drag in the other.
    """

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._specs: dict[str, PluginSpec[T]] = {}

    @property
    def kind(self) -> str:
        return self._kind

    def register(
        self,
        name: str,
        *,
        description: str = "",
        requires_flag: str | None = None,
        tags: tuple[str, ...] = (),
        replace: bool = False,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator registering a factory under `name`.

        A duplicate name raises unless `replace=True`. Silently overwriting
        would make load order decide which implementation runs — a bug that
        only appears when an import moves.
        """

        def decorator(factory: Callable[..., T]) -> Callable[..., T]:
            key = name.strip().lower()
            if key in self._specs and not replace:
                raise ValueError(
                    f"{self._kind} plugin {key!r} is already registered by "
                    f"{self._specs[key].factory!r}. Pass replace=True to override."
                )
            self._specs[key] = PluginSpec(
                name=key,
                factory=factory,
                description=description,
                requires_flag=requires_flag,
                tags=tuple(tags),
            )
            logger.debug("plugin_registered", kind=self._kind, name=key)
            return factory

        return decorator

    def add(self, name: str, factory: Callable[..., T], **kwargs: Any) -> None:
        """Imperative form of `register`, for wiring built elsewhere."""
        self.register(name, **kwargs)(factory)

    def create(self, name: str, /, **kwargs: Any) -> T:
        """Build the named implementation.

        Feature-flag enforcement happens here rather than at call sites so a
        disabled plugin cannot be instantiated by a path that forgot to check.

        `name` is positional-only: without the `/` marker, any factory taking
        a parameter called `name` -- an entirely reasonable thing for a plugin
        to want -- would collide with this method's own argument and fail with
        "got multiple values for argument 'name'".
        """
        spec = self.spec(name)
        if spec.requires_flag and not self._flag_enabled(spec.requires_flag):
            raise PluginDisabledError(self._kind, spec.name, spec.requires_flag)
        return spec.factory(**kwargs)

    def spec(self, name: str, /) -> PluginSpec[T]:
        key = (name or "").strip().lower()
        spec = self._specs.get(key)
        if spec is None:
            raise PluginNotFoundError(self._kind, name, self.names())
        return spec

    def has(self, name: str) -> bool:
        return (name or "").strip().lower() in self._specs

    def names(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[PluginSpec[T]]:
        return [self._specs[name] for name in self.names()]

    def describe(self) -> list[dict]:
        """Serialisable inventory — surfaced by GET /governance/plugins so
        what is actually loaded is inspectable at runtime."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "requires_flag": spec.requires_flag,
                "tags": list(spec.tags),
                "enabled": self._flag_enabled(spec.requires_flag)
                if spec.requires_flag
                else True,
            }
            for spec in self.specs()
        ]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.has(name)

    def __iter__(self) -> Iterator[PluginSpec[T]]:
        return iter(self.specs())

    def __len__(self) -> int:
        return len(self._specs)

    @staticmethod
    def _flag_enabled(flag: str | None) -> bool:
        if not flag:
            return True
        # Imported lazily: feature_flags imports config, and config must not
        # depend on the plugin layer or the import graph becomes a cycle.
        from src.governance.feature_flags import is_enabled

        return is_enabled(flag)


class PluginDisabledError(RuntimeError):
    """Raised when a registered plugin is gated behind a disabled flag.

    Distinct from PluginNotFoundError because the operator response differs:
    "not found" means fix the name or the import, "disabled" means flip the
    flag if you meant to use it.
    """

    def __init__(self, kind: str, name: str, flag: str) -> None:
        super().__init__(
            f"{kind} plugin {name!r} is registered but disabled by feature flag "
            f"{flag!r}. Enable it in settings or via the admin API."
        )
        self.kind = kind
        self.name = name
        self.flag = flag
