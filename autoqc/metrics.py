from .models import LABELS


def metrics(items: list[dict], predictions: dict) -> dict:
    numbers = {"total": len(items), "processed": 0, "inference_errors": 0, "labels": {}, "tags": {}}
    for item in items:
        pred = predictions.get(item["artifact_id"])
        if pred is not None:
            numbers["processed"] += 1
            numbers["inference_errors"] += bool(pred.get("error"))
    for label in LABELS:
        m = {"human_positive": 0, "human_negative": 0, "human_unknown": 0,
             "tp": 0, "fn": 0, "fp": 0, "tn": 0, "model_unknown": 0,
             "inference_errors": 0, "pending": 0}
        for item in items:
            truth = item["labels"].get(label)
            m["human_unknown" if truth is None else "human_positive" if truth else "human_negative"] += 1
            if truth is None:
                continue
            pred = predictions.get(item["artifact_id"])
            if pred is None:
                m["pending"] += 1
            elif pred.get("error"):
                m["inference_errors"] += 1
            elif pred["labels"].get(label) is None:
                m["model_unknown"] += 1
            else:
                value = pred["labels"][label]
                m["tp" if truth and value else "fn" if truth else "fp" if value else "tn"] += 1
        judged = m["tp"] + m["fn"] + m["fp"] + m["tn"]
        m.update({"recall": m["tp"] / (m["tp"] + m["fn"]) if m["tp"] + m["fn"] else None,
                  "precision": m["tp"] / (m["tp"] + m["fp"]) if m["tp"] + m["fp"] else None,
                  "accuracy": (m["tp"] + m["tn"]) / judged if judged else None,
                  "detection_rate": m["tp"] / m["human_positive"] if m["human_positive"] else None,
                  "coverage": judged / (m["human_positive"] + m["human_negative"]) if m["human_positive"] + m["human_negative"] else None})
        numbers["labels"][label] = m
    for index in sorted({item.get("tag_index", item.get("slice_index")) for item in items}):
        group = [item for item in items if item.get("tag_index", item.get("slice_index")) == index]
        numbers["tags"][str(index)] = {
            "total": len(group),
            "processed": sum(i["artifact_id"] in predictions for i in group),
            "inference_errors": sum(bool(predictions.get(i["artifact_id"], {}).get("error")) for i in group)}
    return numbers
