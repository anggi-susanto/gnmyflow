import json
import tomllib
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from studiowright import __version__, paths
from studiowright.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_and_legacy_commands_share_the_existing_namespace():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "gnmyflow"
    assert project["scripts"] == dict.fromkeys(
        ["gnmyflow", "sw", "studiowright"], "studiowright.cli:main"
    )
    assert project["authors"] == [{"name": "Umut Koçak"}]
    assert paths.APP_NAME == "studiowright"
    assert paths.HOME_ENV == "STUDIOWRIGHT_HOME"


def test_plugin_marketplace_and_skill_paths_agree():
    marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["name"] == "gnmyflow"
    plugin_root = ROOT / marketplace["plugins"][0]["source"]
    plugin = json.loads((plugin_root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "gnmyflow"
    assert plugin["version"] == __version__
    assert plugin["author"] == {"name": "Umut Koçak"}
    assert (plugin_root / "skills/gnmyflow/SKILL.md").is_file()
    mcp = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert list(mcp) == ["gnmyflow"]
    assert mcp["gnmyflow"]["args"][1] == (
        "${GNMYFLOW_SOURCE:-git+https://github.com/anggi-susanto/gnmyflow@v"
        + __version__
        + "}"
    )
    assert mcp["gnmyflow"]["args"][2:] == ["gnmyflow", "mcp"]


def test_identity_and_capabilities_do_not_attach_to_chrome():
    with patch(
        "studiowright.chrome.ensure_running", side_effect=AssertionError("Chrome forbidden")
    ):
        runner = CliRunner()
        version = runner.invoke(main, ["--version"])
        assert version.exit_code == 0
        assert version.output.strip() == f"gnmyflow, version {__version__}"
        help_result = runner.invoke(main, ["--help"])
        assert help_result.exit_code == 0
        assert "GnmyFlow" in help_result.output
        capabilities = runner.invoke(main, ["capabilities", "--json"])
        assert capabilities.exit_code == 0
        assert json.loads(capabilities.output)["single_submit"] is True
