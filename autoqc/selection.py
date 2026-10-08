"""Deterministic quota matching, including overlapping issue categories."""
import hashlib
from collections import deque

from .models import LABELS, Recipe


class SelectionError(ValueError):
    def __init__(self, message, availability):
        super().__init__(message)
        self.availability = availability


def select_items(rows: list[dict], recipe: Recipe) -> tuple[list[dict], list[dict]]:
    # Source -> slice quota -> matching artifact -> sink capacity 1. Max-flow
    # prevents a greedy early slice from consuming another slice's only videos.
    rows = sorted(rows, key=lambda r: str(r["artifact_id"]))
    candidates, availability = [], []
    for i, s in enumerate(recipe.tags):
        indices = [j for j, r in enumerate(rows) if
                   r["tags"].get("set") == s.key and
                   (not s.value or r["tags"]["labels"].get(s.value) is True)]
        indices.sort(key=lambda j: hashlib.sha256(
            f'{i}:{rows[j]["artifact_id"]}'.encode()).hexdigest())
        candidates.append(indices)
        availability.append({**s.model_dump(), "available": len(indices)})
    count = len(recipe.tags)
    sink = 1 + count + len(rows)
    graph = [[] for _ in range(sink + 1)]

    def edge(a, b, cap):
        graph[a].append([b, cap, len(graph[b])])
        graph[b].append([a, 0, len(graph[a]) - 1])

    for i, s in enumerate(recipe.tags):
        edge(0, 1 + i, s.count)
        for j in candidates[i]:
            edge(1 + i, 1 + count + j, 1)
    for j in range(len(rows)):
        edge(1 + count + j, sink, 1)
    flow = 0
    while True:
        level = [-1] * len(graph)
        level[0] = 0
        q = deque([0])
        while q:
            v = q.popleft()
            for nxt, cap, _ in graph[v]:
                if cap and level[nxt] < 0:
                    level[nxt] = level[v] + 1
                    q.append(nxt)
        if level[sink] < 0:
            break
        ptr = [0] * len(graph)

        def send(v, n):
            if v == sink:
                return n
            while ptr[v] < len(graph[v]):
                e = graph[v][ptr[v]]
                nxt, cap, rev = e
                if cap and level[nxt] == level[v] + 1:
                    sent = send(nxt, min(n, cap))
                    if sent:
                        e[1] -= sent
                        graph[nxt][rev][1] += sent
                        return sent
                ptr[v] += 1
            return 0

        while (sent := send(0, 3000)):
            flow += sent
    required = sum(s.count for s in recipe.tags)
    if flow != required:
        raise SelectionError(f"Need {required} unique videos, but only {flow} can fill these tags. Add reviewed videos or reduce counts.", availability)
    items = []
    for i in range(count):
        for nxt, cap, _ in graph[1 + i]:
            if nxt > count and nxt != sink and cap == 0:
                row = rows[nxt - 1 - count]
                tags = row["tags"]
                items.append({"generation_id": str(row["uuid"]),
                              "artifact_id": str(row["artifact_id"]), "tag_index": i,
                              "set": tags["set"],
                              "labels": {k: tags["labels"].get(k) for k in LABELS},
                              "metadata": tags.get("metadata", {}),
                              "provenance": tags.get("provenance", {}),
                              "annotation_updated_at": row["updated_at"].isoformat()})
    return items, availability
