"""Prepare local images for upload.

Flow does not finish very large uploads: a 14 MB phone photo never did, and the retry then
uploaded its companion a second time. Large images are therefore scaled down before they
are staged. Their EXIF orientation is applied first; camera and location metadata are not
carried into the smaller copy. Flow scales references down anyway, so nothing is lost.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

#: Files above this size, or with a longer side above MAX_SIDE, are scaled down.
MAX_BYTES = 4 * 1024 * 1024
MAX_SIDE = 3072
JPEG_QUALITY = 92


def stage_for_upload(source: Path, stem: Path) -> Path:
    """Copy ``source`` to ``stem`` plus a suffix, scaling it down when it is too large.

    Returns the staged path. A file Pillow cannot read is copied unchanged.
    """
    try:
        with Image.open(source) as probe:
            too_big = source.stat().st_size > MAX_BYTES or max(probe.size) > MAX_SIDE
    except (UnidentifiedImageError, OSError):
        too_big = False
    if not too_big:
        target = stem.with_suffix(source.suffix.lower())
        if not target.exists():
            shutil.copyfile(source, target)
        return target
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        keep_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
        image = image.convert("RGBA" if keep_alpha else "RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        if keep_alpha:
            target = stem.with_suffix(".png")
            image.save(target, optimize=True)
        else:
            target = stem.with_suffix(".jpg")
            image.save(target, quality=JPEG_QUALITY, optimize=True)
    return target
