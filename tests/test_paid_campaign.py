import json

import pytest

from photo_separator import campaign
from photo_separator.paid_campaign import Budget, chunks, upper_cost
from scripts.real_cover_report import render
from tests.test_campaign import restaurant
from tests.test_images import png


def test_budget_reserves_before_spending_and_keeps_unknown_charge():
    budget = Budget(0.1)
    budget.reserve(0.08)
    with pytest.raises(ValueError):
        budget.reserve(0.03)
    assert budget.used == 0.08
    budget.reconcile(0.08, None)
    with pytest.raises(ValueError):
        budget.reserve(0.03)
    budget.reconcile(0.08, 0.01)
    budget.reserve(0.03)
    assert budget.used == pytest.approx(0.04)


def test_chunking_covers_every_original_once_without_truncation():
    r = restaurant(count=164)
    parts = chunks(r)
    ids = [p["photo_id"] for c in parts for p in c["gallery"]]
    assert len(parts) == 9 and len(ids) == len(set(ids)) == 164
    assert ids == [p["photo_id"] for p in r["gallery"]]
    assert upper_cost(parts[0]["gallery"], (0.3, 1.2)) > 0


def test_visible_cover_crop_preserved_and_rendered_in_actual_table(monkeypatch, tmp_path):
    monkeypatch.setattr(campaign, "download", lambda _: png())
    crop = {"left": 0.5, "top": 0.0, "width": 0.5, "height": 0.5}
    source = {
        "restaurants": [
            {
                "restaurant_id": "1",
                "name": "A",
                "url": "https://example.com/r",
                "gallery": [],
                "baselines": {
                    "frame1": {"declared_url": "https://example.com/sprite", "crop": crop},
                    "frame2": {"declared_url": "https://example.com/sprite", "crop": crop | {"left": 0.0}},
                },
            }
        ]
    }
    frozen = campaign.prepare_campaign(source, tmp_path)
    assert len(frozen["restaurants"][0]["baselines"]) == 2
    render(tmp_path)
    text = (tmp_path / "comparison.html").read_text()
    assert "<table>" in text and "left:-100.00000%" in text
    assert "width:200.00000%" in text
    assert json.loads((tmp_path / "summary.json").read_text())["winner"] is None


def test_panel_samples_all_index_strata_and_marks_truncated_capture():
    from scripts.build_cover_panel import decode_observed_photos, sample_indices

    indices = sample_indices(599, "123")
    assert len(indices) == 20 and len(set(indices)) == 20
    assert indices == sample_indices(599, "123")
    assert all(i * 599 // 20 <= v < (i + 1) * 599 // 20 for i, v in enumerate(indices))
    text = '{"photos":[{"src":"https://example.com/a","id":"a"},{"src":"https://example.com/b"'
    photos, complete = decode_observed_photos(text)
    assert len(photos) == 1 and not complete
    photos, complete = decode_observed_photos('{"photos":[{"src":"https://example.com/a"}]}')
    assert len(photos) == 1 and complete


def test_listing_carousel_hides_pier_and_preserves_real_frame_geometry():
    from photo_separator.baseline_frames import carousel_frames, visible_crop

    crop = visible_crop("height: 300px; transform: translate(calc(-50% - 2px), -20px);", 0.75)
    assert crop["left"] == pytest.approx(0.5 + 2 / 225)
    attrs = {
        "data-collage-type": "4",
        "data-x1": ".499",
        "data-x2": ".499",
        "data-y1": ".499",
        "data-y2": ".5",
        "data-margin-size-x": ".002",
        "data-margin-size-y": ".001",
        "data-first-id": "1",
        "data-pier-index": "3",
        "data-collage-ratio": ".75",
    }
    frames = carousel_frames(
        {"declared_url": "https://example.com/sprite", "crop": crop, "sprite_attributes": attrs}
    )
    assert [f["carousel_slot"] for f in frames] == [1, 2, 0]
    for f in frames:
        c = f["crop"]
        assert 0 <= c["left"] < 1 and 0 <= c["top"] < 1
        assert c["left"] + c["width"] <= 1 and c["top"] + c["height"] <= 1
        assert c["width"] < 1 and c["height"] < 1
        assert f["observed_active_crop"] == crop


def test_judge_rejects_foreign_ids_and_invalid_scores():
    from photo_separator.cover_judge import validate_judge

    value = {
        "photos": [{"photo_id": "p1", "verdict": "keep", "reason": "Food"}],
        "scores": {"CURRENT": 60, "A": 80, "B": 70},
        "preferred": "A",
        "improvement_A": "better",
        "improvement_B": "same",
        "reason": "Clearer",
    }
    assert validate_judge(value, ["p1"]).preferred == "A"
    with pytest.raises(ValueError):
        validate_judge(value, ["other"])
    bad = value | {"scores": {"CURRENT": 60, "A": 101, "B": 70}}
    with pytest.raises(ValueError):
        validate_judge(bad, ["p1"])


def test_judge_sees_clipped_cover_instead_of_full_sprite(tmp_path):
    import base64
    import io

    from PIL import Image

    from photo_separator.cover_judge import jpeg_view

    sprite = Image.new("RGB", (100, 50), "red")
    sprite.paste(Image.new("RGB", (50, 50), (0, 255, 0)), (50, 0))
    sprite.save(tmp_path / "sprite.png")
    photo = {
        "asset": "sprite.png",
        "evidence": {"listing": {"crop": {"left": 0.5, "top": 0, "width": 0.5, "height": 1}}},
    }
    url = jpeg_view(tmp_path, photo, square=True, baseline=True)
    im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    red, green, blue = im.getpixel((112, 112))
    assert im.size == (224, 224) and green > 200 and red < 20
