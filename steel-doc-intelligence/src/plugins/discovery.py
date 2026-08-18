from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from src.monitoring.logger import get_logger

logger = get_logger(__name__)


def load_package(package: str | ModuleType, *, strict: bool = False) -> list[str]:
    """Import every module in a package so its registrations run.

    A registry decorator only executes when its module is imported. Without
    this, dropping `plugins/tools/my_tool.py` into the tree does nothing —
    which defeats the point of a plugin directory.

    `strict=False` (the default) logs and skips a module that fails to
    import rather than aborting the whole scan. That is the right default
    for optional plugins: a web-search tool whose HTTP library is not
    installed should disable that one tool, not prevent the service from
    starting. Set `strict=True` in tests, where a silently skipped plugin
    would make a failure look like a pass.

    Returns the names of the modules that imported successfully.
    """
    module = importlib.import_module(package) if isinstance(package, str) else package
    if not hasattr(module, "__path__"):
        raise ValueError(f"{module.__name__} is a module, not a package — nothing to scan.")

    loaded: list[str] = []
    for info in pkgutil.iter_modules(module.__path__):
        if info.name.startswith("_"):
            continue
        full_name = f"{module.__name__}.{info.name}"
        try:
            importlib.import_module(full_name)
            loaded.append(full_name)
        except Exception as exc:
            if strict:
                raise
            logger.warning("plugin_module_import_failed", module=full_name, error=str(exc))

    logger.info("plugin_package_loaded", package=module.__name__, modules=len(loaded))
    return loaded
