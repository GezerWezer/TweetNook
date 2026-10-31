"""What Gemini must return, and what makes a returned result acceptable."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class MediaTagResult(BaseModel):
    description: str = Field(min_length=1)
    tags: list[str] = Field(min_length=2, max_length=5)

    @field_validator("description")
    @classmethod
    def _description_has_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description is blank")
        return value


class TextTagResult(BaseModel):
    tags: list[str] = Field(min_length=2, max_length=5)


class TaggingPreview(BaseModel):
    """Structured result returned by test runs without persisting tweet tags."""

    id: str
    content_type: Literal["media", "text"]
    model: str
    tweet_type: str
    author_display_name: str
    author_username: str
    text: str
    description: str | None = None
    tags: list[str]


def normalized_tags(tags: list[str], existing_tags: Sequence[str]) -> list[str]:
    canonical = {tag.casefold(): tag for tag in existing_tags}
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        value = str(raw).strip()
        if not value:
            continue
        value = canonical.get(value.casefold(), value)
        folded = value.casefold()
        if folded not in seen:
            normalized.append(value)
            seen.add(folded)
    return normalized


def parse_result_list(response_text: str) -> list[Any]:
    """Free batches answer with a list; raises ValueError for anything else."""
    results = json.loads(response_text)
    if not isinstance(results, list):
        raise ValueError("top-level response must be a list")
    return results
