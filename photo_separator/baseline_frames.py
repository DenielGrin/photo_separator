"""Reproduce the listing's clipped image frame from observed CSS, never show the full sprite."""

import math
import re


def visible_crop(style, aspect_ratio, frame_size=112):
    height_match = re.search(r"height:\s*([\d.]+)px", style)
    transform_match = re.search(r"translate\((.*)\)", style)
    if not height_match or not transform_match:
        raise ValueError("Missing observed frame geometry")
    height = float(height_match[1])
    width = height * aspect_ratio
    if not all(math.isfinite(x) and x > 0 for x in [height, width, frame_size]):
        raise ValueError("Invalid frame dimensions")
    tx, ty = transform_match[1].split(",", 1)

    def offset(text, dim):
        pc = re.search(r"(-?[\d.]+)%", text)
        px = re.search(r"(-?\s*[\d.]+)px", text)
        return (float(pc[1]) * dim / 100 if pc else 0) + (float(px[1].replace(" ", "")) if px else 0)

    return {
        "left": -offset(tx, width) / width,
        "top": -offset(ty, height) / height,
        "width": frame_size / width,
        "height": frame_size / height,
    }


def carousel_frames(evidence, frame_size=112):
    """Reconstruct the site's carousel slots from public data-* coordinates and clip/scale rules.

    Coordinates reproduce the published listing script; they are not original gallery-photo IDs.
    The active captured CSS crop is retained as evidence alongside every derived slot.
    """
    a = evidence.get("sprite_attributes", {})

    def value(key, default=0):
        return float(a.get("data-" + key, default))

    kind = int(value("collage-type", 1))
    x = value("x1")
    x2 = value("x2")
    y = value("y1")
    y2 = value("y2")
    mx = value("margin-size-x")
    my = value("margin-size-y")
    if kind == 4:
        tiles = [
            (0, 0, x, y),
            (x + mx, 0, 1 - x, y),
            (0, y + my, x2, 1 - y),
            (x2 + mx, y + my, 1 - x2, 1 - y),
        ]
    elif kind == 2:
        if value("is-blur"):
            tiles = [(x, y, x2 - x, y2 - y)]
        elif y == 1:
            tiles = [(0, 0, x, 1), (x + mx, 0, 1 - x, 1)]
        else:
            tiles = [(0, 0, 1, y), (0, y + my, 1, 1 - y)]
    elif kind == 3:
        if y == 1 and y2 == 1:
            tiles = [(0, 0, x, 1), (x + mx, 0, x2, 1), (x + x2 + 2 * mx, 0, 1 - x - x2, 1)]
        elif x2 == 1 and y2 == 1:
            tiles = [(0, 0, x, y), (x + mx, 0, 1 - x, y), (0, y + my, 1, 1 - y)]
        else:
            tiles = [(0, 0, 1, y), (0, y + my, x2, 1 - y), (x2 + mx, y + my, 1 - x2, 1 - y)]
    else:
        return [evidence]
    first = int(value("first-id"))
    pier = int(value("pier-index", -1))
    ratio = value("collage-ratio", 1)
    if ratio <= 0:
        return [evidence]
    frames = []
    for n in range(len(tiles)):
        index = (first + n) % len(tiles)
        if index == pier:
            continue
        left, top, w, h = tiles[index]
        if w <= 0 or h <= 0:
            continue
        height = max(frame_size / (w * ratio), frame_size / h) + 12
        width = height * ratio
        crop = {
            "left": left + max(0, (math.floor(w * width) - frame_size) / 2) / width,
            "top": top + max(0, (math.floor(h * height) - frame_size) / 2) / height,
            "width": frame_size / width,
            "height": frame_size / height,
        }
        frames.append(
            evidence
            | {
                "observed_active_crop": evidence.get("crop"),
                "crop": crop,
                "carousel_slot": index,
                "crop_method": "Reconstructed carousel frame using published listing script coordinates, center clipping and +12 CSS-px scaling. Not a separately captured screenshot for every slide.",
            }
        )
    return frames or [evidence]
