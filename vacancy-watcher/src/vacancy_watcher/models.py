"""Pure data models and decision functions used by the browser worker and tests."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ClassSnapshot:
    name: str
    profile_alt: str
    class_id: str | None
    vacancies: int | None
    cells: tuple[str, ...]
    input_present: bool
    input_enabled: bool
    checked: bool = False
    teacher: str = ""

    @property
    def available(self) -> bool:
        return self.vacancies is not None and self.vacancies > 0 and self.input_present and self.input_enabled

    @classmethod
    def from_cells(
        cls,
        cells: Sequence[str],
        *,
        profile_alt: str = "PL",
        class_id: str | None = None,
        input_present: bool = False,
        input_enabled: bool = False,
        checked: bool = False,
    ) -> "ClassSnapshot":
        normalized = tuple(str(cell).strip() for cell in cells)
        vacancies: int | None = None
        if len(normalized) > 3 and re.fullmatch(r"\d+", normalized[3]):
            vacancies = int(normalized[3])
        return cls(
            name=normalized[0] if normalized else "",
            profile_alt=profile_alt,
            class_id=class_id,
            vacancies=vacancies,
            cells=normalized,
            input_present=input_present,
            input_enabled=input_enabled,
            checked=checked,
            teacher=normalized[1] if len(normalized) > 1 else "",
        )


@dataclass(frozen=True)
class CourseSnapshot:
    code: str
    title: str
    classes: tuple[ClassSnapshot, ...] = ()


@dataclass(frozen=True)
class CourseListRow:
    code: str
    title: str
    selected_classes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Decision:
    kind: str
    snapshot: ClassSnapshot | None = None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.kind == "available"


def snapshot_from_mapping(row: Mapping[str, Any]) -> ClassSnapshot:
    """Build a snapshot from a test/browser-neutral row mapping."""

    cells = row.get("cells", ())
    return ClassSnapshot.from_cells(
        cells,
        profile_alt=str(row.get("profile_alt", "PL")),
        class_id=None if row.get("class_id") is None else str(row["class_id"]),
        input_present=bool(row.get("input_present", False)),
        input_enabled=bool(row.get("input_enabled", row.get("input_present", False))),
        checked=bool(row.get("checked", False)),
    )


def decide_target_class(
    classes: Sequence[ClassSnapshot],
    *,
    class_name: str,
    profile_alt: str,
    expected_class_id: str,
) -> Decision:
    """Interpret a target row conservatively; unknown or conflicting data is invalid."""

    matches = [item for item in classes if item.name == class_name and item.profile_alt == profile_alt]
    if not matches:
        return Decision("class_not_found", reason="target class/profile row is absent")
    if len(matches) != 1:
        return Decision("invalid", reason="multiple exact target class/profile rows")
    target = matches[0]
    if target.class_id is not None and target.class_id != expected_class_id:
        return Decision("invalid", target, "target class id differs from the acknowledged id")
    if target.vacancies is None:
        return Decision("invalid", target, "vacancy count is not an integer")
    if target.vacancies < 0:
        return Decision("invalid", target, "vacancy count is negative")
    if target.checked:
        if not target.input_present or target.class_id != expected_class_id:
            return Decision("invalid", target, "checked target does not match the acknowledged control")
        return Decision("already_enrolled", target, "target class is already selected")
    if target.vacancies == 0:
        if target.input_present:
            return Decision("invalid", target, "zero vacancies exposes an unexpected unselected enrollment control")
        return Decision("no_vacancy", target, "target row reports zero vacancies and no checkbox")
    if target.vacancies > 0 and not target.input_present:
        return Decision("invalid", target, "positive vacancy count has no enrollment checkbox")
    if target.vacancies > 0 and not target.input_enabled:
        return Decision("invalid", target, "enrollment checkbox is disabled")
    if target.vacancies > 0 and target.class_id != expected_class_id:
        return Decision("invalid", target, "available target does not expose the acknowledged class id")
    return Decision("available", target)


def authoritative_enrollment_verified(
    rows: Sequence[CourseListRow],
    *,
    course_code: str,
    course_title: str,
    class_name: str,
    profile_alt: str,
) -> bool:
    """Require the exact course and class/profile to be shown on the list page."""

    matches = [row for row in rows if row.code == course_code and row.title == course_title]
    if len(matches) != 1:
        return False
    return any(name == class_name and profile == profile_alt for name, profile in matches[0].selected_classes)
