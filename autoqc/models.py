from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

LABELS = ("bad_product", "bad_movement", "nudity")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Labels(StrictModel):
    bad_product: StrictBool | None = None
    bad_movement: StrictBool | None = None
    nudity: StrictBool | None = None


def set_for(labels: dict) -> str:
    if any(labels.get(k) is True for k in LABELS):
        return "adversarial"
    if all(labels.get(k) is False for k in LABELS):
        return "normal"
    return "unclassified"


class GenerationIn(StrictModel):
    artifact_id: UUID
    labels: Labels
    metadata: dict = Field(default_factory=dict)
    note: str = Field(default="", max_length=2000)


class ReviewIn(StrictModel):
    labels: Labels
    note: str = Field(default="", max_length=2000)
    expected_updated_at: str


class Slice(StrictModel):
    set: Literal["normal", "adversarial"]
    label: Literal["bad_product", "bad_movement", "nudity"] | None = None
    count: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def label_for_set(self):
        if self.set == "adversarial" and not self.label:
            raise ValueError("Adversarial slices need a label")
        if self.set == "normal" and self.label:
            raise ValueError("Normal slices do not take an issue label")
        return self


class Recipe(StrictModel):
    seed: int = Field(default=42, ge=0, le=2147483647)
    slices: list[Slice] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def small_enough(self):
        if sum(s.count for s in self.slices) > 3000:
            raise ValueError("A testset may contain at most 3,000 videos")
        return self


class TestsetIn(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    query: Recipe


class ModeConfig(StrictModel):
    model: str = Field(min_length=1, max_length=200)
    checkpoint: str | None = Field(default=None, max_length=500)
    sampling_fps: float = Field(default=4, gt=0, le=120)
    prompt_version: str = Field(default="autoqc-v1", min_length=1, max_length=160)
    prompt: str = Field(default="Detect bad_product, bad_movement, and nudity according to the supplied label definitions. Return true, false, or null for each label; do not guess.", max_length=20000)
    label_definitions: dict[str, str] = Field(default_factory=lambda: {
        "bad_product": "Visible product identity or appearance differs from the supplied reference product images.",
        "bad_movement": "Unnatural body motion, frozen movement, distorted limbs, or abrupt motion. Normal camera edits alone are not failures.",
        "nudity": "Exposure concern under the reviewed dataset policy; legacy approvals include visible thighs and body contour, not only exposed private parts. Match the human policy supplied for this run."
    })
    preprocessing: dict = Field(default_factory=dict)
    worker_pool: str = Field(default="default", min_length=1, max_length=80)


class ModeIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    config: ModeConfig


class BenchmarkIn(StrictModel):
    mode_ids: list[UUID] = Field(min_length=1, max_length=10)
    testset_id: UUID


class ClaimIn(StrictModel):
    worker_id: str = Field(min_length=1, max_length=120)
    worker_pool: str = Field(default="default", max_length=80)
    models: list[str] = Field(min_length=1, max_length=20)
    lease_seconds: int = Field(default=300, ge=30, le=3600)


class LeaseIn(StrictModel):
    lease_token: UUID


class ResultIn(LeaseIn):
    artifact_id: UUID
    labels: Labels | None = None
    error: str | None = Field(default=None, max_length=4000)
    evidence: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def result_or_error(self):
        if (self.labels is None) == (self.error is None):
            raise ValueError("Supply labels OR an inference error, not both/neither")
        return self


class FailIn(LeaseIn):
    error: str = Field(min_length=1, max_length=4000)
