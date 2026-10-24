import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_dist.py"


@pytest.fixture
def allowed():
    spec = importlib.util.spec_from_file_location("audit_dist", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.allowed


@pytest.mark.parametrize(
    "name",
    [
        ".env", "profiles/default/Default/Cookies", "studiowright.db",
        "captures/session.json", "incidents/page.png", "docs/media/sample.mp4",
        "spikes/probe.py", "src/studiowright/token.json", "../LICENSE", "/LICENSE",
    ],
)
def test_release_allowlist_rejects_unwanted_files(allowed, name):
    assert not allowed(name, wheel=False)
    assert not allowed(name, wheel=True)


def test_release_allowlist_keeps_source_and_license(allowed):
    assert allowed("src/studiowright/cli.py", wheel=False)
    assert allowed("studiowright/cli.py", wheel=True)
    assert allowed("gnmyflow-0.3.1.dist-info/licenses/LICENSE", wheel=True)
    assert allowed("plugins/gnmyflow/.mcp.json", wheel=False)
    assert not allowed("gnmyflow-secret/licenses/LICENSE", wheel=True)
