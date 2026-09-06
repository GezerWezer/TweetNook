"""Presentation-neutral models for archive statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

StatFormat = Literal["integer", "decimal", "percent", "bytes", "date", "datetime", "text"]
SectionKind = Literal["cards", "table", "archive_status", "storage", "tags", "status"]
StatValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class StatItem:
    """One labeled value that each surface can format natively."""

    id: str
    label: str
    value: StatValue
    format: StatFormat = "integer"
    subtitle: str | None = None
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "value": self.value,
            "format": self.format,
            "subtitle": self.subtitle,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class TableColumn:
    """A shared table column definition."""

    key: str
    label: str
    format: StatFormat = "text"
    align: Literal["left", "right"] = "left"

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "format": self.format,
            "align": self.align,
        }


@dataclass(slots=True)
class StatsSection:
    """A report section with generic content and optional specialized data."""

    id: str
    title: str
    kind: SectionKind
    description: str | None = None
    items: list[StatItem] = field(default_factory=list)
    columns: list[TableColumn] = field(default_factory=list)
    rows: list[dict[str, StatValue]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "description": self.description,
            "items": [item.as_dict() for item in self.items],
            "columns": [column.as_dict() for column in self.columns],
            "rows": self.rows,
            "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class StatsSectionSpec:
    """Registry metadata for one ordered statistics section."""

    id: str
    title: str
    kind: SectionKind


@dataclass(slots=True)
class StatsReport:
    """Complete statistics document consumed by both user interfaces."""

    archive_path: str
    owner: str
    generated_at: str
    sections: list[StatsSection]

    def section(self, section_id: str) -> StatsSection:
        for section in self.sections:
            if section.id == section_id:
                return section
        raise KeyError(section_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive_path": self.archive_path,
            "owner": self.owner,
            "generated_at": self.generated_at,
            "sections": [section.as_dict() for section in self.sections],
        }
