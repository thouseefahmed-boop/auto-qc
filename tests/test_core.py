from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from autoqc.metrics import metrics
from autoqc.models import Recipe, set_for
from autoqc.selection import SelectionError, select_items


def row(n, labels):
    return {"uuid": UUID(int=n), "artifact_id": UUID(int=n),
            "tags": {"set": set_for(labels), "labels": labels},
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}


def recipe(*slices):
    return Recipe(tags=[{"key": "adversarial", "value": label, "count": count} for label, count in slices])


def test_unknown_is_not_normal():
    assert set_for({"bad_product": False}) == "unclassified"
    assert set_for(dict.fromkeys(["bad_product", "bad_movement", "nudity"], False)) == "normal"
    assert set_for({"nudity": True}) == "adversarial"


def test_overlap_is_unique_and_feasible():
    rows = [row(1, {"bad_product": True, "nudity": True}), row(2, {"bad_product": True})]
    items, _ = select_items(rows, recipe(("bad_product", 1), ("nudity", 1)))
    assert len({i["artifact_id"] for i in items}) == 2
    assert next(i for i in items if i["tag_index"] == 1)["artifact_id"] == str(UUID(int=1))


def test_deterministic_even_when_input_reordered():
    rows = [row(n, {"bad_product": True}) for n in range(1, 30)]
    first, _ = select_items(rows, recipe(("bad_product", 8)))
    second, _ = select_items(list(reversed(rows)), recipe(("bad_product", 8)))
    assert first == second


def test_shortage_after_dedup_is_reported():
    with pytest.raises(SelectionError, match="unique videos"):
        select_items([row(1, {"bad_product": True, "nudity": True})], recipe(("bad_product", 1), ("nudity", 1)))


def test_missing_normal_not_fabricated():
    with pytest.raises(SelectionError):
        select_items([row(1, {"bad_product": False})], Recipe(tags=[{"key": "normal", "count": 1}]))


def test_recipe_validation():
    with pytest.raises(ValidationError):
        Recipe(tags=[{"key": "adversarial", "count": 1}])
    with pytest.raises(ValidationError):
        Recipe(tags=[{"key": "normal", "value": "nudity", "count": 1}])


def test_metrics_unknown_error_pending_and_confusion():
    vals = [True, True, True, True, True, False, False, None]
    items = [{"artifact_id": str(n), "labels": {"nudity": v}, "tag_index": 0} for n, v in enumerate(vals)]
    preds = {"0": {"labels": {"nudity": True}}, "1": {"labels": {"nudity": False}},
             "2": {"labels": {"nudity": None}}, "3": {"error": "timeout"},
             "5": {"labels": {"nudity": True}}, "6": {"labels": {"nudity": False}},
             "7": {"labels": {"nudity": True}}}
    result = metrics(items, preds)
    m = result["labels"]["nudity"]
    assert [m[k] for k in ("tp", "fn", "fp", "tn")] == [1, 1, 1, 1]
    assert m["human_positive"] == 5 and m["human_unknown"] == 1
    assert m["inference_errors"] == 1 and m["model_unknown"] == 1 and m["pending"] == 1
    assert m["detection_rate"] == .2 and m["recall"] == .5 and m["precision"] == .5
    assert result["processed"] == 7 and result["inference_errors"] == 1


def test_no_results_have_no_invented_precision():
    result = metrics([{"artifact_id": "a", "labels": {"nudity": True}, "tag_index": 0}], {})
    assert result["labels"]["nudity"]["precision"] is None
    assert result["labels"]["nudity"]["coverage"] == 0
