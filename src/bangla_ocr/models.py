from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class OCRLine:
    text: str
    bbox: list[float]
    confidence: float | None = None
    label: str = "Text"


@dataclass
class OCRCandidate:
    engine: str
    variant: str
    text: str
    lines: list[OCRLine] = field(default_factory=list)
    confidence: float | None = None
    score: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageDecision:
    selected_candidate: int | None
    selected_engine: str | None
    selected_variant: str | None
    status: str
    include: bool
    page_role: str
    reasons: list[str]
    disagreement: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
