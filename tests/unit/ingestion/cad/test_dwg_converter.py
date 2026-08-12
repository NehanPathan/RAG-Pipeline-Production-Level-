import pytest

from src.config import Settings
from src.ingestion.cad.dwg_converter import (
    DwgConversionUnavailableError,
    NullDwgConverter,
    OdaFileConverter,
)
from src.ingestion.cad.registry import get_dwg_converter


class TestNullConverter:
    """DWG support is not configured by default, because the ODA converter
    cannot be redistributed in our image."""

    def test_reports_itself_unavailable(self):
        assert NullDwgConverter().available() is False

    async def test_the_error_names_the_remedy(self, tmp_path):
        """A user who uploads a DWG should be told to export DXF, not shown a
        parse failure from a library handed a file it cannot read."""
        with pytest.raises(DwgConversionUnavailableError) as exc:
            NullDwgConverter().to_dxf(tmp_path / "x.dwg", tmp_path)

        assert "Export the drawing" in str(exc.value)
        assert "DXF" in str(exc.value)


class TestOdaConverter:
    def test_unavailable_when_the_binary_is_absent(self):
        converter = OdaFileConverter(executable="/nonexistent/ODAFileConverter")

        assert converter.available() is False

    def test_converting_without_the_binary_names_the_setting(self, tmp_path):
        converter = OdaFileConverter(executable="/nonexistent/ODAFileConverter")

        with pytest.raises(DwgConversionUnavailableError, match="CAD_ODA_CONVERTER_PATH"):
            converter.to_dxf(tmp_path / "x.dwg", tmp_path / "out")

    def test_invokes_the_cli_with_the_documented_argument_order(self, tmp_path):
        """ODA's CLI takes positional arguments in an order documented
        nowhere, and it converts a directory rather than a file."""
        source = tmp_path / "S-104.dwg"
        source.write_bytes(b"not really a dwg")
        dest = tmp_path / "out"
        commands: list[list[str]] = []

        def _runner(command: list[str], timeout: float) -> int:
            commands.append(command)
            # Stand in for the converter writing its output.
            (dest / "S-104.dxf").write_text("0\nSECTION\n")
            return 0

        converter = OdaFileConverter(executable=str(tmp_path), runner=_runner)
        produced = converter.to_dxf(source, dest)

        assert produced == dest / "S-104.dxf"
        # argv: exe, input dir, output dir, version, format, recurse, audit
        assert commands[0][4] == "DXF"
        assert commands[0][5] == "0", "must not recurse"

    def test_missing_output_is_an_error_not_a_silent_pass(self, tmp_path):
        """A zero exit code with no output file means the DWG was unreadable;
        continuing would hand the reader a file that does not exist."""
        source = tmp_path / "S-104.dwg"
        source.write_bytes(b"x")

        converter = OdaFileConverter(
            executable=str(tmp_path), runner=lambda command, timeout: 0
        )

        with pytest.raises(DwgConversionUnavailableError, match="without producing"):
            converter.to_dxf(source, tmp_path / "out")


class TestRegistry:
    def _settings(self, **overrides) -> Settings:
        return Settings(_env_file=None, **overrides)

    def test_defaults_to_no_dwg_support(self):
        assert get_dwg_converter(self._settings()).name == "none"

    def test_oda_can_be_selected(self):
        converter = get_dwg_converter(self._settings(cad_dwg_converter="oda"))

        assert converter.name == "oda"

    def test_a_missing_binary_does_not_stop_startup(self):
        """DXF still works, and a deployment that never receives a DWG should
        not fail to boot over a converter it does not need."""
        converter = get_dwg_converter(
            self._settings(cad_dwg_converter="oda", cad_oda_converter_path="/nonexistent")
        )

        assert converter.available() is False

    def test_an_unknown_choice_falls_back_to_null(self):
        assert get_dwg_converter(self._settings(cad_dwg_converter="magic")).name == "none"
