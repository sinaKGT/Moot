"""Basic checks that the package and the `moot` command work."""

import moot
from moot.cli import main


def test_version_is_set() -> None:
    assert moot.__version__ == "0.1.0"


def test_cli_version(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "moot 0.1.0"


def test_cli_default_runs(capsys) -> None:
    assert main([]) == 0
    assert "Moot 0.1.0" in capsys.readouterr().out
