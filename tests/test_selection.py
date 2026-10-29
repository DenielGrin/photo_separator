from photo_separator.schemas import Assessment, Policy
from photo_separator.selection import choose_covers, decide


def assessment(**changes):
    values = dict(
        category="interior",
        quality=90,
        appeal=90,
        relevance=95,
        confidence=0.96,
        people="none",
        text_dominant=False,
        severe_defect=False,
        unsafe=False,
        reason="Чёткий интерьер",
    )
    return Assessment(**(values | changes))


def test_document_cannot_be_rescued_by_beauty():
    assert (
        decide(assessment(category="menu", appeal=100), {"width": 1000, "height": 1000}, Policy())[0]
        == "reject"
    )


def test_background_people_go_to_review_and_prominent_people_rejected():
    for people, decision in [("background", "review"), ("prominent", "reject"), ("none", "keep")]:
        assert decide(assessment(people=people), {"width": 1000, "height": 1000}, Policy())[0] == decision


def test_confidence_is_gate_and_not_part_of_quality_score():
    confident = decide(assessment(), {"width": 1000, "height": 1000}, Policy())
    uncertain = decide(assessment(confidence=0.2), {"width": 1000, "height": 1000}, Policy())
    assert confident[0] == "keep" and uncertain[0] == "review"
    assert confident[1] == uncertain[1]


def test_unsafe_and_severe_defects_are_rejected():
    for changes in [{"unsafe": True}, {"severe_defect": True}, {"people": "prominent"}]:
        assert decide(assessment(**changes), {"width": 1000, "height": 1000}, Policy())[0] == "reject"


def test_bad_relevance_cannot_be_rescued_by_appeal():
    assert (
        decide(assessment(relevance=10, appeal=100, quality=100), {"width": 1000, "height": 1000}, Policy())[
            0
        ]
        == "reject"
    )


def test_dont_fill_to_three_with_rejected_photos():
    rows = [
        {"decision": "keep", "score": 90, "url": "a", "index": 0, "assessment": {"category": "food"}},
        {"decision": "reject", "score": 100, "url": "b", "index": 1, "assessment": {"category": "people"}},
    ]
    assert choose_covers(rows, Policy()) == ["a"]


def test_diversity_includes_interior_among_food():
    rows = [
        {"decision": "keep", "score": 95 - i, "url": str(i), "index": i, "assessment": {"category": "food"}}
        for i in range(8)
    ]
    rows.append(
        {"decision": "keep", "score": 80, "url": "inside", "index": 9, "assessment": {"category": "interior"}}
    )
    chosen = choose_covers(rows, Policy())
    assert len(chosen) == 6 and "inside" in chosen
