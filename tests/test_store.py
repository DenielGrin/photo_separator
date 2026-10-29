import pytest

from photo_separator.store import Conflict, Store


def run(rid="r", urls=None):
    urls = urls or ["a", "b", "c", "d"]
    return {
        "run_id": "run1",
        "restaurant_id": rid,
        "cover": urls,
        "semantic_model_used": True,
        "results": [
            {
                "url": u,
                "decision": "keep",
                "score": 90 - i,
                "reason_code": "cover_candidate",
                "metrics": {"width": 1000},
            }
            for i, u in enumerate(urls)
        ],
    }


def test_manual_overrides_survive_new_run_and_restart(tmp_path):
    path = tmp_path / "test.db"
    store = Store(path)
    state = store.mutate("r", 0, run=run())
    state = store.mutate("r", state["revision"], manual=("b", "exclude"))
    state = store.mutate("r", state["revision"], manual=("d", "pin"))
    state = Store(path).mutate("r", state["revision"], run=run(urls=["a", "b", "c"]))
    assert state["cover"] == ["d", "a", "c"] and state["excluded"] == ["b"]


def test_revision_conflict_and_other_restaurant_protected(tmp_path):
    s = Store(tmp_path / "t.db")
    s.mutate("r", 0, run=run())
    with pytest.raises(Conflict):
        s.mutate("r", 0, run=run())
    with pytest.raises(ValueError):
        s.mutate("r", 1, run=run(rid="other"))
    with pytest.raises(ValueError):
        s.mutate("r", 1, manual=("foreign", "pin"))


def test_bad_run_cannot_erase_saved_cover(tmp_path):
    s = Store(tmp_path / "t.db")
    s.mutate("r", 0, run=run())
    bad = run()
    bad["results"][0]["reason_code"] = "provider_error"
    with pytest.raises(ValueError):
        s.mutate("r", 1, run=bad)
    assert s.get("r")["revision"] == 1


def test_exactly_six_pin_limit_is_atomic(tmp_path):
    s = Store(tmp_path / "t.db")
    state = s.mutate("r", 0, run=run(urls=list("abcdefg")))
    for u in "abcdef":
        state = s.mutate("r", state["revision"], manual=(u, "pin"))
    with pytest.raises(ValueError):
        s.mutate("r", state["revision"], manual=("g", "pin"))
    assert s.get("r")["pinned"] == list("abcdef")
