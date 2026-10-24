"""Check an installed artifact without attaching to Chrome or contacting providers."""

import json
import socket
import sys
import sysconfig
from importlib.metadata import distribution
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

import studiowright
from studiowright import chrome


def forbidden(*args, **kwargs):
    raise AssertionError("Browser and network access are forbidden in the install smoke test")


def main_check():
    package = distribution("gnmyflow")
    assert package.version == studiowright.__version__
    source = Path(studiowright.__file__).resolve()
    assert source.is_relative_to(Path(sys.prefix).resolve()), source
    assert "site-packages" in source.parts, source
    scripts = Path(sysconfig.get_path("scripts"))
    for name in ("gnmyflow", "sw", "studiowright"):
        executable = scripts / (name + (".exe" if sys.platform == "win32" else ""))
        assert executable.is_file(), executable
        entry = next(ep for ep in package.entry_points if ep.name == name)
        assert entry.group == "console_scripts"
        assert entry.value == "studiowright.cli:main"
        with (
            patch.object(chrome, "ensure_running", forbidden),
            patch.object(chrome, "require_chrome", forbidden),
            patch.object(socket.socket, "connect", forbidden),
            patch.object(socket.socket, "connect_ex", forbidden),
        ):
            runner = CliRunner()
            for args in (["--help"], ["--version"], ["capabilities", "--json"]):
                result = runner.invoke(entry.load(), args, prog_name=name)
                assert result.exit_code == 0, (name, args, result.output, result.exception)
                if args == ["--version"]:
                    assert result.output.strip() == f"gnmyflow, version {package.version}"
                if args == ["capabilities", "--json"]:
                    assert json.loads(result.output)["single_submit"] is True
    print(f"Installed gnmyflow {package.version}: all three CLI entry points passed offline")


if __name__ == "__main__":
    main_check()
