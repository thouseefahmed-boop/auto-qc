from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StrictBool, model_validator

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


class TagSelection(StrictModel):
    key: Literal["normal", "adversarial"]
    value: Literal["bad_product", "bad_movement", "nudity"] | None = None
    count: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def label_for_set(self):
        if self.key == "adversarial" and not self.value:
            raise ValueError("Adversarial tags need a value such as bad_product")
        if self.key == "normal" and self.value:
            raise ValueError("Normal tags do not take an issue value")
        return self


class Recipe(StrictModel):
    tags: list[TagSelection] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def small_enough(self):
        if sum(s.count for s in self.tags) > 3000:
            raise ValueError("A testset may contain at most 3,000 videos")
        return self


class TestsetIn(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    query: Recipe


class ModeIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)


class WorkerSettingsIn(StrictModel):
    endpoint: AnyHttpUrl
    served_model: str = Field(min_length=1, max_length=200)
    fps: float | None = Field(default=None, gt=0)
    num_frames: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def one_sampling_setting(self):
        if self.fps is not None and self.num_frames is not None:
            raise ValueError("Set FPS or fixed frame count, not both")
        if self.fps is None and self.num_frames is None:
            raise ValueError("Set an FPS or fixed frame count")
        return self


class RecipePromptIn(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=20000)
    label_definitions: dict[str, str] = Field(default_factory=dict)


class BenchmarkIn(StrictModel):
    mode_ids: list[UUID] = Field(min_length=1, max_length=10)
    testset_id: UUID
    recipe_id: UUID


class ClaimIn(StrictModel):
    worker_id: str = Field(min_length=1, max_length=120)
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
