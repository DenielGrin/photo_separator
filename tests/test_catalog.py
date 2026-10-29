from photo_separator.catalog import free_vision_models


def candidate(**changes):
    return {
        "id": "google/example:free",
        "name": "Example",
        "architecture": {"input_modalities": ["text", "image"]},
        "pricing": {"prompt": "0", "completion": "0"},
        "supported_parameters": ["response_format"],
    } | changes


def test_catalog_only_includes_free_images_and_json():
    payload = {
        "data": [
            candidate(),
            candidate(id="paid/model", pricing={"prompt": "0.001", "completion": "0.002"}),
            candidate(id="text/model:free", architecture={"input_modalities": ["text"]}),
            candidate(id="no-json/model:free", supported_parameters=[]),
            candidate(id="image-fee/model:free", pricing={"prompt": "0", "completion": "0", "image": "0.01"}),
            candidate(id="bad/model:free", pricing={"prompt": "NaN", "completion": "0"}),
            candidate(id="unknown/model:free", pricing={}),
        ]
    }
    rows = free_vision_models(payload)
    assert [r["model"] for r in rows] == ["google/example:free"]
    assert rows[0]["input_price"] == 0 and rows[0]["catalog_verified_free"]
