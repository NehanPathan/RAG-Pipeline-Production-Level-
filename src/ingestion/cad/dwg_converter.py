"""DWG to DXF conversion.

DWG is a closed format and `ezdxf` cannot read it. The options, and why this
one:

* **ODA File Converter** (used here). Free of charge, best coverage of DWG
  versions from R12 to current. Its licence grants free *use* but not
  *redistribution*, so the binary cannot be baked into an image we publish --
  the deployer installs it themselves, under their own acceptance of ODA's
  terms. That is a deployment constraint, not an engineering one, and it is
  why the converter is discovered at runtime rather than assumed.
* **LibreDWG.** GPL-3.0. Invoking it as a subprocess would not infect our
  code, but shipping an on-prem image containing it would trigger source
  disclosure obligations. Weaker coverage of recent DWG versions, and no
  usable Windows build. Registered as an alternative for hosted-only
  deployments, after counsel signs off.
* **Autodesk Platform Services.** Uploads the client's structural drawings to
  Autodesk. Under an engineering NDA that is usually a non-starter, quite
  apart from the per-conversion cost.

When no converter is available the failure is explicit and actionable -- the
uploader is told to export DXF -- rather than a stack trace from a library
that was handed a file it cannot parse.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from src.monitoring.logger import get_logger

logger = get_logger(__name__)

# Injected so tests never need the binary, matching the pattern
# TesseractProvider uses for its page loader.
Runner = Callable[[list[str], float], int]


class DwgConversionUnavailableError(RuntimeError):
    """No DWG converter is installed, or the configured one is missing."""


class DwgConverter(Protocol):
    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def to_dxf(self, source: Path, dest_dir: Path) -> Path: ...


def _run(command: list[str], timeout: float) -> int:
    completed = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        logger.warning(
            "dwg_converter_nonzero_exit",
            returncode=completed.returncode,
            stderr=completed.stderr.decode(errors="replace")[:400],
        )
    return completed.returncode


class OdaFileConverter:
    """Wraps the ODA File Converter CLI.

    Its argument order is positional and undocumented in any man page:
      <input dir> <output dir> <version> <format> <recurse> <audit> [filter]
    Converting a *directory* rather than a file is the only mode it offers,
    so the source is isolated in its own directory first.
    """

    def __init__(
        self,
        executable: str,
        timeout_seconds: float = 120.0,
        output_version: str = "ACAD2018",
        runner: Runner | None = None,
    ) -> None:
        self._executable = executable
        self._timeout = timeout_seconds
        self._output_version = output_version
        self._run = runner or _run

    @property
    def name(self) -> str:
        return "oda"

    def available(self) -> bool:
        return bool(self._executable) and (
            Path(self._executable).exists() or shutil.which(self._executable) is not None
        )

    def to_dxf(self, source: Path, dest_dir: Path) -> Path:
        if not self.available():
            raise DwgConversionUnavailableError(
                f"ODA File Converter not found at {self._executable!r}. Install it and set "
                "CAD_ODA_CONVERTER_PATH, or upload the drawing exported as DXF."
            )

        dest_dir.mkdir(parents=True, exist_ok=True)
        staging = dest_dir / "_dwg_in"
        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / source.name
        if staged.resolve() != source.resolve():
            shutil.copy2(source, staged)

        code = self._run(
            [
                self._executable,
                str(staging),
                str(dest_dir),
                self._output_version,
                "DXF",
                "0",  # do not recurse
                "1",  # audit and repair
            ],
            self._timeout,
        )

        produced = dest_dir / f"{source.stem}.dxf"
        if not produced.exists():
            raise DwgConversionUnavailableError(
                f"ODA File Converter exited {code} without producing {produced.name}. "
                "The DWG may be corrupt or of an unsupported version."
            )
        logger.info("dwg_converted", source=source.name, output=produced.name)
        return produced


class NullDwgConverter:
    """Stands in when DWG support is not configured.

    Present rather than absent so the loader has one code path, and so the
    error a user sees names the actual remedy instead of surfacing as a
    parse failure deep inside a CAD library.
    """

    @property
    def name(self) -> str:
        return "none"

    def available(self) -> bool:
        return False

    def to_dxf(self, source: Path, dest_dir: Path) -> Path:
        raise DwgConversionUnavailableError(
            "DWG support is not configured on this deployment. Export the drawing "
            "as DXF and upload that, or ask an administrator to install the ODA "
            "File Converter."
        )
