"""Модель занятия — общая для парсера, БД, бота и .ics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)


@dataclass(frozen=True, slots=True)
class Lesson:
    date: date
    start: str  # "09:00"
    end: str = ""  # "10:30"
    group: str = ""
    subject: str = ""
    room: str = ""
    teacher: str = ""
    subgroup: str = ""

    # --- ключи ---------------------------------------------------------
    @property
    def key(self) -> tuple:
        """Логический ключ занятия: по нему считается дифф и обновление."""
        return (self.date, self.start, self.group, self.subgroup)

    @property
    def full_key(self) -> tuple:
        return (self.date, self.start, self.group, self.subgroup, self.subject, self.room, self.teacher)

    @property
    def weekday(self) -> str:
        return WEEKDAYS[self.date.weekday()]

    @property
    def start_time(self) -> time:
        hh, mm = self.start.split(":")
        return time(int(hh), int(mm))

    @property
    def end_time(self) -> time | None:
        if not self.end:
            return None
        hh, mm = self.end.split(":")
        return time(int(hh), int(mm))

    def begins_at(self, tz) -> datetime:
        return datetime.combine(self.date, self.start_time, tzinfo=tz)

    def is_over(self, now: datetime) -> bool:
        end = self.end_time
        if end is None:
            return False
        return datetime.combine(self.date, end, tzinfo=now.tzinfo) <= now

    def time_range(self) -> str:
        return f"{self.start}–{self.end}" if self.end else self.start

    def with_(self, **kwargs) -> Lesson:
        return replace(self, **kwargs)

    def as_row(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "start": self.start,
            "end": self.end,
            "group": self.group,
            "subject": self.subject,
            "room": self.room,
            "teacher": self.teacher,
            "subgroup": self.subgroup,
        }

    @classmethod
    def from_row(cls, row: dict) -> Lesson:
        return cls(
            date=date.fromisoformat(row["date"]),
            start=row["start"],
            end=row.get("end") or "",
            group=row.get("group") or "",
            subject=row.get("subject") or "",
            room=row.get("room") or "",
            teacher=row.get("teacher") or "",
            subgroup=row.get("subgroup") or "",
        )


@dataclass(frozen=True, slots=True)
class ParseResult:
    lessons: tuple[Lesson, ...]
    groups: tuple[str, ...]
    dates: tuple[date, ...]
    warnings: tuple[str, ...] = ()
    pages: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.lessons


def week_bounds(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)
