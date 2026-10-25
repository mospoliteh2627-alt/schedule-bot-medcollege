"""Тексты сообщений и .ics — всё, что видит пользователь."""

from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime, tzinfo

from .importer import Diff
from .models import WEEKDAYS, Lesson, week_bounds

MONTHS_SHORT = (
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)
WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def esc(value: str) -> str:
    return html.escape(value or "", quote=False)


def dash(value: str, empty: str = "—") -> str:
    return esc(value) if value else empty


def day_title(day: date) -> str:
    today = date.today()
    prefix = ""
    if day == today:
        prefix = "Сегодня · "
    elif (day - today).days == 1:
        prefix = "Завтра · "
    return f"{prefix}{WEEKDAYS[day.weekday()]}, {day:%d.%m}"


def lesson_line(number: int, lesson: Lesson, *, with_teacher: bool = True, with_group: bool = False) -> str:
    parts = [f"<b>{number}.</b> <code>{lesson.time_range()}</code>", esc(lesson.subject or "—")]
    if with_group and lesson.group:
        parts.insert(1, f"<b>{esc(lesson.group)}</b>")
    if lesson.room:
        parts.append(f"каб. {esc(lesson.room)}")
    if lesson.subgroup:
        parts.append(f"({esc(lesson.subgroup)})")
    text = " · ".join(parts)
    if with_teacher and lesson.teacher:
        text += f"\n    <i>{esc(lesson.teacher)}</i>"
    return text


def day_block(lessons: list[Lesson], *, number_from: int = 1, with_teacher: bool = True, with_group: bool = False) -> str:
    lines = []
    for i, lesson in enumerate(lessons, start=number_from):
        lines.append(lesson_line(i, lesson, with_teacher=with_teacher, with_group=with_group))
    return "\n".join(lines)


def render_day(group: str, day: date, lessons: list[Lesson]) -> str:
    header = f"📅 <b>{esc(group)}</b> · {day_title(day)}"
    if not lessons:
        return f"{header}\n\nСвободно — пар нет 🎉"
    return f"{header}\n\n{day_block(lessons)}"


def render_week(group: str, lessons: list[Lesson], *, limit: int = 3800) -> list[str]:
    if not lessons:
        return [f"📅 <b>{esc(group)}</b>\n\nНа эту неделю расписания пока нет."]
    by_day: dict[date, list[Lesson]] = {}
    for lesson in lessons:
        by_day.setdefault(lesson.date, []).append(lesson)

    blocks: list[str] = []
    for day in sorted(by_day):
        blocks.append(
            f"<b>{day_title(day)}</b>\n{day_block(by_day[day], with_teacher=False)}"
        )

    monday, sunday = week_bounds(lessons[0].date)
    header = f"📅 <b>{esc(group)}</b> · неделя {monday:%d.%m}–{sunday:%d.%m}\n"
    return _chunk(header, blocks, limit)


def _chunk(header: str, blocks: list[str], limit: int) -> list[str]:
    messages: list[str] = []
    current = header
    continuation = header.rstrip() + " (продолжение)\n"
    for block in blocks:
        candidate = f"{current}\n{block}\n"
        if len(candidate) > limit and len(current) > len(header):
            messages.append(current.rstrip())
            current = f"{continuation}\n{block}\n"
        else:
            current = candidate
    if current.strip():
        messages.append(current.rstrip())
    return messages


def render_next(group: str, lessons: list[Lesson], now: datetime) -> str:
    upcoming = [lesson for lesson in lessons if not lesson.is_over(now) or lesson.begins_at(now.tzinfo) > now]
    if not upcoming:
        return f"📅 <b>{esc(group)}</b>\n\nВпереди нет пар в загруженном расписании."
    lesson = upcoming[0]
    if lesson.begins_at(now.tzinfo) <= now:
        head = "▶️ <b>Сейчас идёт</b>"
    else:
        head = "⏭ <b>Следующая пара</b> " + relative_when(lesson.begins_at(now.tzinfo) - now)
    return (
        f"{head} · <b>{esc(group)}</b>\n\n"
        f"{lesson_line(1, lesson)}\n\n"
        f"<i>{lesson.weekday}, {lesson.date:%d.%m.%Y}</i>"
    )


def relative_when(delta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 0:
        return "сейчас"
    if minutes < 60:
        return f"через {minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"через {hours} ч {minutes} мин" if minutes else f"через {hours} ч"
    days = hours // 24
    return f"через {days} дн"


def render_bells(lessons: list[Lesson]) -> str:
    pairs: dict[tuple[str, str], int] = {}
    for lesson in lessons:
        pairs[(lesson.start, lesson.end)] = pairs.get((lesson.start, lesson.end), 0) + 1
    if not pairs:
        return "Расписание звонков появится вместе с расписанием."
    lines = ["🔔 <b>Звонки</b>\n"]
    for i, ((start, end), _count) in enumerate(sorted(pairs.items()), start=1):
        lines.append(f"{i}. <code>{start}–{end or '??'}</code>")
    return "\n".join(lines)


def render_changes(diff: Diff, group: str | None = None) -> str:
    part = diff.for_group(group) if group else diff
    if part.is_empty:
        return ""
    head = f"🔔 <b>Изменения в расписании</b>{f' · {esc(group)}' if group else ''}\n"
    lines: list[str] = []
    for old, new in part.changed:
        before = " · ".join(x for x in (old.subject, f"каб. {old.room}" if old.room else "") if x)
        after = " · ".join(x for x in (new.subject, f"каб. {new.room}" if new.room else "") if x)
        who = f" ({new.teacher})" if new.teacher and new.teacher != old.teacher else ""
        lines.append(
            f"✏️ {new.date:%d.%m} {new.start} · {esc(new.group)}: <s>{esc(before)}</s> → <b>{esc(after)}</b>{esc(who)}"
        )
    for lesson in part.added:
        lines.append(
            f"➕ {lesson.date:%d.%m} {lesson.start} · {esc(lesson.group)}: "
            f"<b>{esc(lesson.subject or '—')}</b>"
            + (f", каб. {esc(lesson.room)}" if lesson.room else "")
            + (f" — {esc(lesson.teacher)}" if lesson.teacher else "")
        )
    for lesson in part.removed:
        lines.append(
            f"➖ {lesson.date:%d.%m} {lesson.start} · {esc(lesson.group)}: "
            f"<s>{esc(lesson.subject or '—')}</s> отменено"
        )
    if not lines:
        return ""
    return head + "\n" + "\n".join(lines[:40])


def render_search(query: str, lessons: list[Lesson], limit: int = 15) -> str:
    if not lessons:
        return f"По запросу «{esc(query)}» ничего не нашлось."
    lines = [f"🔎 «{esc(query)}» — {len(lessons)} совпадений\n"]
    for lesson in lessons[:limit]:
        lines.append(
            f"{lesson.date:%d.%m} {lesson.start} · <b>{esc(lesson.group)}</b> · "
            f"{esc(lesson.subject or '—')}"
            + (f" · каб. {esc(lesson.room)}" if lesson.room else "")
            + (f" · {esc(lesson.teacher)}" if lesson.teacher else "")
        )
    if len(lessons) > limit:
        lines.append(f"…и ещё {len(lessons) - limit}")
    return "\n".join(lines)


def render_import_preview(preview, *, max_rows: int = 12) -> str:
    result = preview.result
    diff = preview.diff
    head = f"📄 <b>{esc(preview.name)}</b>\n"
    facts = [
        f"страниц: {result.pages}",
        f"занятий: {len(result.lessons)}",
        f"групп: {len(result.groups)}",
        f"дней: {len(result.dates)}",
    ]
    if result.dates:
        facts.append(f"период: {result.dates[0]:%d.%m}–{result.dates[-1]:%d.%m.%Y}")
    text = head + " · ".join(facts) + "\n"

    if preview.mode == "all":
        text += "Режим: <b>полная замена</b> расписания\n"
    else:
        text += "Режим: <b>замена дат из файла</b> (остальные дни не трогаем)\n"

    if diff.is_empty:
        text += "\n♻️ Содержимое не изменилось — публиковать нечего.\n"
    else:
        first_time = (
            not diff.removed and not diff.changed and len(diff.added) == len(result.lessons)
        )
        if first_time:
            text += "\n🆕 Все занятия новые — это первая публикация этих дат.\n"
        else:
            text += (
                f"\nИзменения: <b>+{len(diff.added)}</b> новых, "
                f"<b>−{len(diff.removed)}</b> удалено, "
                f"<b>✏️ {len(diff.changed)}</b> изменено\n"
            )
            rows = 0
            for old, new in diff.changed[:max_rows]:
                rows += 1
                text += (
                    f"✏️ {new.date:%d.%m} {new.start} {esc(new.group)}: "
                    f"{esc(old.subject)} → <b>{esc(new.subject)}</b>\n"
                )
            for lesson in diff.added[: max(0, max_rows - rows)]:
                rows += 1
                text += (
                    f"➕ {lesson.date:%d.%m} {lesson.start} {esc(lesson.group)}: "
                    f"{esc(lesson.subject or '—')}\n"
                )
            for lesson in diff.removed[: max(0, max_rows - rows)]:
                rows += 1
                text += f"➖ {lesson.date:%d.%m} {lesson.start} {esc(lesson.group)}: {esc(lesson.subject)}\n"
            if diff.size > rows:
                text += f"…ещё {diff.size - rows}\n"

    if result.warnings:
        text += "\n⚠️ <b>Предупреждения парсера</b>\n"
        for warning in result.warnings[:6]:
            text += f"• {esc(warning)}\n"
    if len(text) > 3800:
        text = text[:3800] + "…"
    return text


def render_import_done(preview, source_id: int, notified: int) -> str:
    result = preview.result
    return (
        f"✅ Опубликовано: <b>{esc(preview.name)}</b>\n"
        f"Версия #{source_id} · занятий {len(result.lessons)} · "
        f"групп {len(result.groups)} · дней {len(result.dates)}\n"
        f"Уведомлений отправлено: {notified}\n\n"
        f"Откатить: <code>/rollback {source_id}</code>"
    )


# ---------------------------------------------------------------------- .ics
def _ics_escape(value: str) -> str:
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ics_fold(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts: list[str] = []
    current = ""
    for char in line:
        if len((current + char).encode("utf-8")) > 74:
            parts.append(current)
            current = char
        else:
            current += char
    parts.append(current)
    return "\r\n ".join(parts)


def to_ics(lessons: list[Lesson], tz: tzinfo, calendar_name: str = "Расписание") -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//schedule-bot//RU//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + _ics_escape(calendar_name),
    ]
    for lesson in lessons:
        start = datetime.combine(lesson.date, lesson.start_time, tzinfo=tz).astimezone(UTC)
        end_time = lesson.end_time
        end = (
            datetime.combine(lesson.date, end_time, tzinfo=tz).astimezone(UTC)
            if end_time
            else start.replace(hour=(start.hour + 1) % 24)
        )
        subject = lesson.subject or "Занятие"
        title = f"{subject} · {lesson.group}" if lesson.group else subject
        description = " ".join(
            part for part in (lesson.teacher, lesson.subgroup) if part
        )
        uid = f"{lesson.date:%Y%m%d}-{lesson.start.replace(':', '')}-{lesson.group}-{abs(hash(lesson.full_key)) % 10**8}@schedule-bot"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
                f"DTEND:{end:%Y%m%dT%H%M%SZ}",
                "SUMMARY:" + _ics_escape(title),
                "LOCATION:" + _ics_escape(f"каб. {lesson.room}" if lesson.room else ""),
                "DESCRIPTION:" + _ics_escape(description),
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


def human_groups(groups: list[str]) -> str:
    return ", ".join(groups) if groups else "—"


def normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())
