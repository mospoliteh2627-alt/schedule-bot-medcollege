"""Прикладные операции над расписанием (без Telegram)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .models import Lesson, week_bounds
from .store import Store


@dataclass(slots=True)
class Context:
    store: Store
    config: Config

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.config.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)


def day_lessons(store: Store, group: str, day: date) -> list[Lesson]:
    return store.lessons_for_group(group, day, day)


def week_lessons(store: Store, group: str, ref: date) -> tuple[date, date, list[Lesson]]:
    monday, sunday = week_bounds(ref)
    return monday, sunday, store.lessons_for_group(group, monday, sunday)


def upcoming_week(store: Store, group: str, ref: date) -> tuple[date, date, list[Lesson]]:
    """Неделя с парами: текущая, иначе ближайшая следующая."""
    monday, sunday, lessons = week_lessons(store, group, ref)
    if lessons:
        return monday, sunday, lessons
    for offset in range(1, 9):
        candidate = monday + timedelta(weeks=offset)
        m, s, lessons = week_lessons(store, group, candidate)
        if lessons:
            return m, s, lessons
    return monday, sunday, []


def next_lesson(store: Store, group: str, now: datetime) -> Lesson | None:
    """Ближайшая пара: идущая сейчас или следующая по времени."""
    horizon = now.date() + timedelta(days=21)
    lessons = store.lessons_for_group(group, now.date(), horizon)
    for lesson in lessons:
        if not lesson.is_over(now):
            return lesson
    return None


def is_running(lesson: Lesson, now: datetime) -> bool:
    return lesson.begins_at(now.tzinfo) <= now and not lesson.is_over(now)


def search(store: Store, query: str) -> list[Lesson]:
    from .formatting import normalize_query

    needle = normalize_query(query)
    if len(needle) < 2:
        return []
    words = [w for w in needle.split() if len(w) >= 2]
    result: list[Lesson] = []
    for lesson in store.lessons():
        haystack = normalize_query(
            " ".join([lesson.subject, lesson.teacher, lesson.room, lesson.group])
        )
        if all(word in haystack for word in words):
            result.append(lesson)
    return result


def find_teachers(store: Store, query: str) -> list[str]:
    """Преподаватели, в имени которых встречается запрос («Шамухаммедов», «кабанова»)."""
    from .formatting import normalize_query

    needle = normalize_query(query)
    if len(needle) < 3:
        return []
    return [t for t in store.teachers() if needle in normalize_query(t)]


def teacher_week(store: Store, teacher: str, ref: date) -> tuple[date, date, list[Lesson]]:
    monday, sunday = week_bounds(ref)
    return monday, sunday, store.lessons_for_teacher(teacher, monday, sunday)


def current_period(store: Store) -> tuple[date, date] | None:
    dates = store.dates()
    if not dates:
        return None
    return dates[0], dates[-1]


def groups_with_lessons(store: Store) -> list[str]:
    return store.groups()
