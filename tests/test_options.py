from __future__ import annotations

import pytest

from studiowright.flow import options as o


def test_image_defaults_are_nano_banana_2_1_landscape_x1() -> None:
    opts = o.ImageOptions.build()
    assert (opts.model, opts.aspect, opts.count) == (
        o.ImageModel.NANO_BANANA_2_1,
        o.Aspect.LANDSCAPE,
        1,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("nano-banana-pro", o.ImageModel.NANO_BANANA_PRO),
        ("NANO_BANANA_2_LITE", o.ImageModel.NANO_BANANA_2_LITE),
    ],
)
def test_image_model_accepts_value_or_name(value: str, expected: o.ImageModel) -> None:
    assert o.ImageOptions.build(model=value).model is expected


@pytest.mark.parametrize("aspect", ["16:9", "4:3", "1:1", "3:4", "9:16"])
def test_every_image_aspect_has_an_icon(aspect: str) -> None:
    assert o.ImageOptions.build(aspect=aspect).aspect.icon.startswith("crop_")


def test_unknown_values_name_the_choices() -> None:
    with pytest.raises(o.OptionError, match="16:9"):
        o.ImageOptions.build(aspect="2:1")
    with pytest.raises(o.OptionError, match="count"):
        o.ImageOptions.build(count=5)


def test_video_rejects_image_only_aspects() -> None:
    with pytest.raises(o.OptionError, match="16:9 or 9:16"):
        o.VideoOptions.build(aspect="1:1")


def test_omni_validates_duration_and_resolution() -> None:
    opts = o.VideoOptions.build(model="omni-flash", resolution="360p", duration=4)
    assert (opts.resolution, opts.duration) == ("360p", 4)
    with pytest.raises(o.OptionError, match="duration"):
        o.VideoOptions.build(duration=5)
    with pytest.raises(o.OptionError, match="resolution"):
        o.VideoOptions.build(resolution="1080p")


def test_veo_drops_length_and_resolution() -> None:
    opts = o.VideoOptions.build(model="veo-quality", resolution="360p", duration=4)
    assert (opts.resolution, opts.duration) == (None, None)


@pytest.mark.parametrize(
    ("text", "matches"), [("8s", True), ("8 sn.", True), ("18 sn.", False), ("80s", False)]
)
def test_duration_pattern_is_locale_proof(text: str, matches: bool) -> None:
    assert bool(o.duration_pattern(8).match(text)) is matches


def test_count_pattern_is_exact() -> None:
    assert o.count_pattern(2).match(" x2 ")
    assert not o.count_pattern(2).match("x20")
