"""Импорт PDF: разбор → предпросмотр → дифф с текущим → публикация."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .models import Lesson, ParseResult
from .pdf_parser import ScheduleParser
from .store import Store


@dataclass(frozen=True, slots=True)
class Diff:
    mode: str
    dates: tuple[date, ...]
    added: tuple[Lesson, ...] = ()
    removed: tuple[Lesson, ...] = ()
    changed: tuple[tuple[Lesson, Lesson], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    @property
    def size(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)

    def groups(self) -> list[str]:
        names = {lesson.group for lesson in self.added} | {lesson.group for lesson in self.removed}
        names |= {new.group for _, new in self.changed}
        return sorted(names)

    def for_group(self, group: str) -> Diff:
        return Diff(
            mode=self.mode,
            dates=self.dates,
            added=tuple(lesson for lesson in self.added if lesson.group == group),
            removed=tuple(lesson for lesson in self.removed if lesson.group == group),
            changed=tuple((o, n) for o, n in self.changed if n.group == group),
        )


@dataclass(frozen=True, slots=True)
class ImportPreview:
    result: ParseResult
    diff: Diff
    mode: str
    name: str
    filename: str
    sha256: str

    @property
    def ok(self) -> bool:
        """Импорт считается удачным, если нашлись занятия и есть даты."""
        return bool(self.result.lessons) and bool(self.result.dates)


def build_diff(current: list[Lesson], incoming: list[Lesson], mode: str = "dates") -> Diff:
    scope = {lesson.date for lesson in incoming}
    base = current if mode == "all" else [lesson for lesson in current if lesson.date in scope]
    base_index = {lesson.key: lesson for lesson in base}
    new_index = {lesson.key: lesson for lesson in incoming}

    added = tuple(new_index[k] for k in new_index.keys() - base_index.keys())
    removed = tuple(base_index[k] for k in base_index.keys() - new_index.keys())
    changed = []
    for key in new_index.keys() & base_index.keys():
        old, new = base_index[key], new_index[key]
        if (old.subject, old.room, old.teacher, old.end) != (new.subject, new.room, new.teacher, new.end):
            changed.append((old, new))

    order = lambda lesson: (lesson.date, lesson.start, lesson.group, lesson.subgroup)  # noqa: E731
    return Diff(
        mode=mode,
        dates=tuple(sorted(scope)),
        added=tuple(sorted(added, key=order)),
        removed=tuple(sorted(removed, key=order)),
        changed=tuple(sorted(changed, key=lambda p: order(p[1]))),
    )


def _default_name(dates: tuple[date, ...], filename: str) -> str:
    if dates:
        if len(dates) == 1:
            return f"Расписание на {dates[0]:%d.%m.%Y}"
        return f"Расписание с {dates[0]:%d.%m.%Y} по {dates[-1]:%d.%m.%Y}"
    return Path(filename).stem or "Расписание"


def prepare(
    store: Store,
    payload: bytes,
    filename: str,
    *,
    mode: str = "dates",
    name: str = "",
    default_year: int | None = None,
) -> ImportPreview:
    parser = ScheduleParser(default_year=default_year)
    result = parser.parse_bytes(payload)
    diff = build_diff(store.lessons(), list(result.lessons), mode)
    return ImportPreview(
        result=result,
        diff=diff,
        mode=mode,
        name=name or _default_name(result.dates, filename),
        filename=filename,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def publish(store: Store, preview: ImportPreview, user_id: int | None = None) -> int:
    return store.publish(
        preview.result.lessons,
        scope_dates=preview.diff.dates or preview.result.dates,
        mode=preview.mode,
        name=preview.name,
        filename=preview.filename,
        sha256=preview.sha256,
        user_id=user_id,
    )
