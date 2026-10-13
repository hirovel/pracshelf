"""Search evidence shared by the current intake and historical experiments."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .collector import CollectionError, web_url


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str
    title: str = Field(min_length=1, max_length=1000)
    snippet: str = Field(default="", max_length=6000)
    author: str | None = Field(default=None, max_length=500)
    published: str | None = Field(default=None, max_length=200)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        try:
            return web_url(value)
        except CollectionError as error:
            raise ValueError(str(error)) from error


class SearchObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    provider: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    status: Literal["succeeded", "failed"]
    results: list[SearchHit] = Field(default_factory=list, max_length=5)
    provider_output: str = Field(min_length=1, max_length=500000)
    limitations: str = Field(min_length=1, max_length=2000)
    reported_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_observation(self):
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        if self.status == "failed" and self.results:
            raise ValueError("Failed searches cannot contain successful results")
        for hit in self.results:
            if hit.url not in self.provider_output:
                raise ValueError("Each result URL must occur in the supplied provider output")
        return self
