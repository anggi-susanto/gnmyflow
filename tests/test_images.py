from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from PIL import Image

from studiowright import __version__
from studiowright.images import MAX_SIDE, stage_for_upload

ROOT = Path(__file__).resolve().parents[1]


def test_small_images_are_copied_as_they_are(tmp_path: Path) -> None:
    source = tmp_path / "ref.PNG"
    Image.new("RGB", (640, 360), "teal").save(source)
    staged = stage_for_upload(source, tmp_path / "sw-0123456789")
    assert staged.name == "sw-0123456789.png"
    assert staged.read_bytes() == source.read_bytes()


def test_large_photos_are_scaled_down_upright(tmp_path: Path) -> None:
    source = tmp_path / "phone.jpg"
    photo = Image.new("RGB", (6000, 4000), "orange")
    exif = photo.getexif()
    exif[0x0112] = 6  # orientation: rotate 90 degrees to display
    photo.save(source, exif=exif)
    staged = stage_for_upload(source, tmp_path / "sw-abcdef0123")
    with Image.open(staged) as image:
        assert staged.suffix == ".jpg"
        assert max(image.size) == MAX_SIDE
        assert image.size[1] > image.size[0]  # the EXIF rotation was applied
        assert 0x0112 not in image.getexif()


def test_large_images_keep_transparency(tmp_path: Path) -> None:
    source = tmp_path / "logo.png"
    Image.new("RGBA", (5000, 5000), (255, 0, 0, 0)).save(source)
    staged = stage_for_upload(source, tmp_path / "sw-fedcba9876")
    with Image.open(staged) as image:
        assert staged.suffix == ".png" and image.mode == "RGBA"
        assert image.size == (MAX_SIDE, MAX_SIDE)


def test_versions_agree() -> None:
    """The package, the plugin and the release the plugin's MCP server runs are one version."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads(
        (ROOT / "plugins/studiowright/.claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    mcp = (ROOT / "plugins/studiowright/.mcp.json").read_text(encoding="utf-8")
    pinned = re.search(r"studiowright@v(\d+\.\d+\.\d+)", mcp)
    assert pinned is not None
    assert project["project"]["version"] == __version__ == plugin["version"] == pinned.group(1)
