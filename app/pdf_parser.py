"""PDF → занятия.

Схема таблицы, под которую рассчитан парсер (так устроены PDF медколледжа):

    ┌────────────┬─────────┬─────────┬─────────┐
    │            │ 101С    │ 102С    │ 103С    │   ← шапка: группы по столбцам
    ├────────────┼─────────┼─────────┼─────────┤
    │ 14.09.2026 │         │         │         │   ← заголовок дня
    │ Понедельник│         │         │         │
    │ 09:00-10:30│ Матем.  │ Химия   │ ...     │
    │            │ 203     │ 308     │         │
    │            │Кабанова │Никитина │         │   ← преподаватель под предметом
    └────────────┴─────────┴─────────┴─────────┘

Алгоритм: слова с координатами → строки → шапка групп (столбцы) → строки времени
(ряды) → ячейка = слова в пересечении столбца и ряда → предмет/аудитория/преподаватель.
Никаких «магических» отступов: границы столбцов — середины между центрами групп,
границы рядов — середины между строками времени.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pymupdf

from .config import DEFAULT_GROUP_RE
from .models import Lesson, ParseResult

DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$")
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})$")
# обрезанное время в узкой колонке: «9:00-10:», «10:40-12», «14:20»
TIME_START_RE = re.compile(r"^(\d{1,2})[:.](\d{2})(?=$|\s*[-–—])")
ROOM_RE = re.compile(r"^(?:каб\.?\s*)?(\d{2,3}[а-яёА-ЯЁ]?)$")
SURNAME_RE = re.compile(r"^[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]{2,})?$")
INITIALS_RE = re.compile(r"^[А-ЯЁ]\.\s?[А-ЯЁ]?\.?$")
SINGLE_INITIAL_RE = re.compile(r"^[А-ЯЁ]\.?$")
SUBGROUP_RE = re.compile(r"(\d)\s*п\s*/\s*г", re.IGNORECASE)
WEEKDAYS_LOWER = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
}
# слова, которые не могут быть предметом
NOISE = {
    "расписание",
    "занятий",
    "группа",
    "группы",
    "день",
    "пара",
    "пары",
    "перемена",
    "обед",
    "аудитория",
    "кабинет",
    "преподаватель",
    "итого",
    "примечание",
}
# Обрезанные в узких колонках названия (вариант → каноническое). Ключи в нижнем регистре.
SUBJECT_CANON: list[tuple[str, str]] = [
    ("введение в профессию", "Введение в профессию"),
    ("ведение в профессию", "Введение в профессию"),
    ("в профессию", "Введение в профессию"),
    ("об и защиты родины", "ОБ и защита Родины"),
    ("об и защита родины", "ОБ и защита Родины"),
    ("и защиты родины", "ОБ и защита Родины"),
    ("основы микробиологии", "Основы микробиологии"),
    ("микробиологии", "Основы микробиологии"),
    ("основы фармакологии", "Основы фармакологии"),
    ("с осн.фармакологии", "Основы фармакологии"),
]


SUBJECT_KEYWORDS: list[tuple[str, str]] = [
    ("микробиолог", "Основы микробиологии"),
    ("фармаколог", "Основы фармакологии"),
]


def _canon_subject(text: str) -> str:
    low = text.lower()
    for variant, canonical in SUBJECT_CANON:
        if low == variant or (len(low) >= 5 and variant.startswith(low)):
            return canonical
    for needle, canonical in SUBJECT_KEYWORDS:
        if needle in low:
            return canonical
    return text


@dataclass(slots=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(0.5, self.y1 - self.y0)


@dataclass(slots=True)
class Line:
    words: list[Word] = field(default_factory=list)

    @property
    def cy(self) -> float:
        return sum(w.cy for w in self.words) / len(self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def h(self) -> float:
        return max(w.h for w in self.words)


def _lines_of(words: list[Word]) -> list[Line]:
    """Кластеризация слов в строки по вертикали (y растёт вниз)."""
    if not words:
        return []
    heights = sorted(w.h for w in words)
    tol = max(1.0, heights[len(heights) // 2] * 0.65)
    ordered = sorted(words, key=lambda w: (w.cy, w.x0))
    lines: list[Line] = []
    for w in ordered:
        if lines and abs(w.cy - lines[-1].cy) <= tol:
            lines[-1].words.append(w)
        else:
            lines.append(Line([w]))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


def _to_date(token: str, default_year: int) -> date | None:
    m = DATE_RE.match(token)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
    y = default_year if not year else (2000 + int(year) if len(year) == 2 else int(year))
    try:
        return date(y, month, day)
    except ValueError:
        return None


def _hhmm(h: str, m: str) -> str:
    return f"{int(h):02d}:{m}"


def _clean_subject(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw).strip(" ,;:—-·")
    text = SUBGROUP_RE.sub("", text).strip(" ,;:—-·")
    text = re.sub(r"\s+д$", " (дист.)", text)
    return _canon_subject(text)


class ScheduleParser:
    def __init__(self, group_regex: str = DEFAULT_GROUP_RE, default_year: int | None = None):
        self.group_re = re.compile(group_regex)
        self.default_year = default_year or date.today().year

    # ------------------------------------------------------------------
    def parse_file(self, path: str | Path) -> ParseResult:
        return self.parse_bytes(Path(path).read_bytes())

    def parse_bytes(self, data: bytes) -> ParseResult:
        lessons: list[Lesson] = []
        warnings: list[str] = []
        pages = 0
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            pages = doc.page_count
            for pno in range(pages):
                page = doc[pno]
                words = [
                    Word(text=t, x0=x0, y0=y0, x1=x1, y1=y1)
                    for x0, y0, x1, y1, t, *_ in page.get_text("words")
                    if t.strip()
                ]
                page_lessons, page_warn = self._parse_page(words, pno + 1)
                lessons.extend(page_lessons)
                warnings.extend(page_warn)

        # дедупликация → нормализация года → снова дедупликация (даты могли схлопнуться)
        unique: dict[tuple, Lesson] = {}
        for lesson in lessons:
            unique.setdefault(lesson.full_key, lesson)
        distinct, year_warnings = _normalize_years(list(unique.values()))
        warnings.extend(year_warnings)
        distinct, subject_warnings = _normalize_subjects(distinct)
        warnings.extend(subject_warnings)
        unique = {}
        for lesson in distinct:
            unique.setdefault(lesson.full_key, lesson)
        ordered = sorted(unique.values(), key=lambda lesson: (lesson.date, lesson.start, lesson.group, lesson.subgroup))

        missing_teacher = sum(1 for lesson in ordered if not lesson.teacher)
        if missing_teacher:
            warnings.append(
                f"{missing_teacher} занятий без преподавателя — так в PDF "
                f"({missing_teacher * 100 // max(1, len(ordered))}% занятий)"
            )
        groups = sorted({lesson.group for lesson in ordered if lesson.group}, key=_group_sort_key)
        dates = sorted({lesson.date for lesson in ordered})
        if not ordered:
            warnings.append(
                "Не найдено ни одного занятия. Проверьте, что PDF содержит таблицу "
                "«группы × дни» с временем пар (текстовый слой, не скан)."
            )
        return ParseResult(
            lessons=tuple(ordered),
            groups=tuple(groups),
            dates=tuple(dates),
            warnings=tuple(warnings),
            pages=pages,
        )

    # ------------------------------------------------------------------
    def _parse_page(self, words: list[Word], page_no: int) -> tuple[list[Lesson], list[str]]:
        """Разбор страницы: шапка групп → колонки; время → ряды; ячейка = колонка × ряд.

        Реальные PDF колледжа: колонка «Дата | Время» слева, даты стоят по центру
        своего блока, предмет на строку выше времени, преподаватель — на строку ниже.
        """
        warnings: list[str] = []
        if not words:
            return [], [f"стр. {page_no}: нет текстового слоя (скан?)"]

        lines = _lines_of(words)

        # --- шапка: строка с максимальным числом групп
        group_line = max(
            lines, key=lambda ln: sum(1 for w in ln.words if self.group_re.match(w.text))
        )
        header = [w for w in group_line.words if self.group_re.match(w.text)]
        if len(header) < 2:
            return [], [f"стр. {page_no}: не найдена шапка с группами"]

        groups = sorted(({"name": w.text, "cx": w.cx} for w in header), key=lambda g: g["cx"])
        if len({g["name"] for g in groups}) != len(groups):
            warnings.append(f"стр. {page_no}: группы в шапке повторяются")

        col_widths = [groups[i + 1]["cx"] - groups[i]["cx"] for i in range(len(groups) - 1)]
        col_w = sorted(col_widths)[len(col_widths) // 2] if col_widths else 60.0
        left_limit = groups[0]["cx"] - col_w * 0.55
        cols: list[tuple[float, float]] = []
        for i, group in enumerate(groups):
            lo = (groups[i - 1]["cx"] + group["cx"]) / 2 if i > 0 else group["cx"] - col_w / 2
            hi = (
                (groups[i + 1]["cx"] + group["cx"]) / 2
                if i + 1 < len(groups)
                else group["cx"] + col_w / 2
            )
            cols.append((lo, hi))

        # --- даты: обычно в левой колонке и по центру своего блока
        marks: list[tuple[date, float, float]] = []
        for w in words:
            if not DATE_RE.match(w.text):
                continue
            parsed = _to_date(w.text, self.default_year)
            if parsed:
                marks.append((parsed, w.cy, w.cx))
        in_column = [m for m in marks if m[2] <= left_limit]
        if in_column:
            marks = in_column
        if not marks:
            warnings.append(f"стр. {page_no}: не найдены даты")
            return [], warnings
        # убираем дубли (двойной текстовый слой / повторы на странице)
        deduped: list[tuple[date, float, float]] = []
        for mark in sorted(marks, key=lambda m: m[1]):
            if deduped and abs(mark[1] - deduped[-1][1]) < 1.0 and mark[0] == deduped[-1][0]:
                continue
            deduped.append(mark)
        marks = deduped

        # --- время пар: левее первой группы
        # время — это подписи рядов; берём все подходящие слова (на разных страницах
        # колонка времени стоит чуть по-разному), из ячеек их исключаем ниже
        time_words = [w for w in words if TIME_START_RE.match(w.text)]
        if not time_words:
            warnings.append(f"стр. {page_no}: не найдено время пар")
            return [], warnings

        rows: list[dict] = []
        full_times: dict[str, Counter] = {}
        for w in sorted(time_words, key=lambda w: w.cy):
            if rows and abs(w.cy - rows[-1]["y"]) < 1.0:
                continue
            full = TIME_RE.match(w.text)
            if full:
                start = _hhmm(full.group(1), full.group(2))
                end = _hhmm(full.group(3), full.group(4))
                full_times.setdefault(start, Counter())[end] += 1
            else:
                part = TIME_START_RE.match(w.text)
                assert part is not None
                start, end = _hhmm(part.group(1), part.group(2)), ""
            rows.append({"start": start, "end": end, "y": w.cy})
        if not rows:
            return [], [f"стр. {page_no}: ряды времени пустые"]
        # обрезанные ярлыки («9:00-10:») добираем каноническим окончанием из полных
        canon = {start: counter.most_common(1)[0][0] for start, counter in full_times.items()}
        truncated = 0
        for row in rows:
            if not row["end"]:
                row["end"] = canon.get(row["start"], "")
                truncated += 1
        if truncated:
            warnings.append(
                f"стр. {page_no}: у {truncated} пар время обрезано в PDF — окончание взято по другим дням"
            )

        gaps = [rows[i + 1]["y"] - rows[i]["y"] for i in range(len(rows) - 1)]
        pitch = sorted(gaps)[len(gaps) // 2] if gaps else 10.0

        # дата ряда — ближайшая по вертикали (метка стоит по центру блока)
        date_gaps = [marks[i + 1][1] - marks[i][1] for i in range(len(marks) - 1)]
        block_h = sorted(date_gaps)[len(date_gaps) // 2] if date_gaps else pitch * 8
        for row in rows:
            mark = min(marks, key=lambda m: abs(m[1] - row["y"]))
            row["date"] = mark[0]
            if abs(mark[1] - row["y"]) > max(block_h * 0.65, pitch * 3):
                warnings.append(
                    f"стр. {page_no}: пара {row['start']} далеко от даты {mark[0]:%d.%m} — проверьте"
                )

        # дубли «дата + время» (один и тот же ряд на разных слоях)
        seen: set[tuple] = set()
        unique_rows: list[dict] = []
        for row in rows:
            key = (row["date"], row["start"])
            if key in seen:
                continue
            seen.add(key)
            unique_rows.append(row)
        rows = unique_rows

        # --- слова → (колонка группы, ряд)
        cell_words: dict[tuple[int, int], list[Word]] = {}
        for line in lines:
            if line.cy <= group_line.cy:
                continue
            ri = min(range(len(rows)), key=lambda i: abs(rows[i]["y"] - line.cy))
            if abs(rows[ri]["y"] - line.cy) > pitch * 0.9:
                continue
            prev_col = -1
            prev_x1 = -1e9
            for w in line.words:
                if _is_label(w.text):
                    continue
                gi = _column_of(w, cols)
                if gi < 0 and ROOM_RE.match(w.text) and prev_col >= 0 and w.x0 - prev_x1 < 8:
                    gi = prev_col  # аудитория «прилипла» к предмету в соседнем столбце
                if gi < 0:
                    continue
                cell_words.setdefault((gi, ri), []).append(w)
                if not ROOM_RE.match(w.text):
                    prev_col, prev_x1 = gi, w.x1

        # --- разбор ячеек
        lessons: list[Lesson] = []
        for (gi, ri), words_in_cell in sorted(cell_words.items()):
            cell_lines = _lines_of(words_in_cell)
            cell_lessons, cell_warn = self._parse_cell(cell_lines, rows[ri], groups[gi]["name"], page_no)
            lessons.extend(cell_lessons)
            warnings.extend(cell_warn)
        return lessons, warnings

    # ------------------------------------------------------------------
    def _parse_cell(
        self, lines: list[Line], row: dict, group: str, page_no: int
    ) -> tuple[list[Lesson], list[str]]:
        """Ячейка → занятия. Строки идут сверху вниз: предмет (1–2 строки), затем преподаватель.

        В одной ячейке может быть несколько записей: подгруппы («1.МДК…», «2.Физика»)
        или пара без преподавателя (так бывает в PDF — не выдумываем данные).
        """
        warnings: list[str] = []
        lessons: list[Lesson] = []
        subject_parts: list[str] = []
        room = ""
        subgroup = ""

        def emit(teacher: str) -> None:
            nonlocal subject_parts, room, subgroup
            subject = _clean_subject(" ".join(subject_parts))
            if _is_noise(subject):
                subject = ""
            if subject or teacher:
                lessons.append(
                    Lesson(
                        date=row["date"],
                        start=row["start"],
                        end=row["end"],
                        group=group,
                        subject=subject,
                        room=room,
                        teacher=teacher,
                        subgroup=subgroup,
                    )
                )
            subject_parts, room, subgroup = [], "", ""

        for line in lines:
            teachers, leftovers = self._teachers_in_line(line)
            text, line_room, line_subgroup = self._split_subject(leftovers)

            # фамилия без инициалов в конце ячейки: предмет уже набран выше
            if (
                not teachers
                and not text
                and not line_room
                and not line_subgroup
                and (subject_parts or room)
                and len(leftovers) == 1
                and SURNAME_RE.match(leftovers[0].text)
            ):
                emit(leftovers[0].text)
                continue

            # новая подгруппа — предыдущая запись закрывается
            if line_subgroup and (subject_parts or room):
                emit("")

            if text:
                subject_parts.append(text)
            room = line_room or room
            subgroup = line_subgroup or subgroup

            if teachers:
                names = ", ".join(
                    f"{surname} {initials}".strip() for surname, initials in teachers
                )
                emit(names)

        emit("")
        # «Информ. Шамухаммедов»: фамилия уехала в предмет, инициалы потерялись
        for index, lesson in enumerate(lessons):
            if lesson.teacher or " " not in lesson.subject:
                continue
            head, _, tail = lesson.subject.rpartition(" ")
            if _looks_like_surname(tail):
                lessons[index] = lesson.with_(subject=head.strip(), teacher=tail)
        for lesson in lessons:
            if not lesson.subject:
                warnings.append(
                    f"стр. {page_no}, {group}, {row['start']}: преподаватель без предмета"
                )
        return lessons, warnings

    def _split_subject(self, words: list[Word]) -> tuple[str, str, str]:
        """Слова строки → (текст предмета, аудитория, подгруппа)."""
        tokens = [w.text for w in words]
        parts: list[str] = []
        room = ""
        subgroup = ""
        i = 0
        while i < len(tokens):
            token = tokens[i]
            # «1.» отдельным словом: «1. МДК.01.02»
            if (
                re.fullmatch(r"[1-9][.)]", token)
                and i + 1 < len(tokens)
                and re.match(r"^[А-ЯЁA-Zа-яё]", tokens[i + 1])
            ):
                subgroup = f"{token[0]} п/г"
                i += 1
                continue
            marker = re.match(r"^([1-9])[.)]\s*([А-ЯЁA-Zа-яё].*)$", token)
            if marker:
                subgroup = f"{marker.group(1)} п/г"
                parts.append(marker.group(2))
                i += 1
                continue
            inline = SUBGROUP_RE.search(token)
            if inline:
                subgroup = f"{inline.group(1)} п/г"
                i += 1
                continue
            if (
                token.isdigit()
                and i + 1 < len(tokens)
                and re.fullmatch(r"п\s*/\s*г\.?", tokens[i + 1], re.IGNORECASE)
            ):
                subgroup = f"{token} п/г"
                i += 2
                continue
            # аудитория, приклеенная к предмету: «микробиол104», «207сновы»
            glued_head = re.match(r"^(\d{2,3})([А-Яа-яЁё][^\d]*)$", token)
            if glued_head:
                room = glued_head.group(1)
                token = glued_head.group(2)
            glued_tail = re.match(r"^([А-Яа-яЁё][^.]*?)(\d{2,3})$", token)
            if glued_tail:
                room = glued_tail.group(2)
                token = glued_tail.group(1)
            rm = ROOM_RE.match(token)
            if rm:
                room = rm.group(1)
                i += 1
                continue
            parts.append(token)
            i += 1
        return " ".join(parts), room, subgroup

    def _teachers_in_line(self, line: Line) -> tuple[list[tuple[str, str]], list[Word]]:
        """Ищет пары «Фамилия И.О.» в строке, возвращает их и остальные слова."""
        teachers: list[tuple[str, str]] = []
        used: set[int] = set()
        words = line.words
        for i, w in enumerate(words):
            if i in used:
                continue
            if not SURNAME_RE.match(w.text):
                continue
            j = i + 1
            if j >= len(words) or j in used:
                continue
            nxt = words[j]
            if not (INITIALS_RE.match(nxt.text) or SINGLE_INITIAL_RE.match(nxt.text)):
                continue
            teachers.append((w.text, _normalize_initials(nxt.text)))
            used.update({i, j})
        leftovers = [w for idx, w in enumerate(words) if idx not in used]
        # одинокие инициалы не считаем частью предмета
        leftovers = [
            w
            for w in leftovers
            if not (INITIALS_RE.match(w.text) or SINGLE_INITIAL_RE.match(w.text))
        ]
        return teachers, leftovers


SURNAME_ENDINGS = (
    "ов", "ев", "ёв", "ин", "ын", "ский", "цкий", "ова", "ева", "ина",
    "ко", "ук", "юк", "ян", "дзе", "швили", "ая", "ий",
)


def _looks_like_surname(text: str) -> bool:
    low = text.lower()
    return bool(re.fullmatch(r"[А-ЯЁ][а-яё\-]{3,}", text)) and low.endswith(SURNAME_ENDINGS)


def _is_label(text: str) -> bool:
    """Служебные подписи (время, дата, день недели, имя группы) — не содержимое ячейки."""
    if TIME_START_RE.match(text) or DATE_RE.match(text):
        return True
    return text.lower() in WEEKDAYS_LOWER


def _column_of(word: Word, cols: list[tuple[float, float]]) -> int:
    """Индекс столбца с максимальным горизонтальным перекрытием (-1 — вне сетки)."""
    best, best_overlap = -1, 0.0
    for i, (lo, hi) in enumerate(cols):
        overlap = min(word.x1, hi) - max(word.x0, lo)
        if overlap > best_overlap:
            best, best_overlap = i, overlap
    return best if best_overlap > 0 else -1


def _normalize_years(lessons: list[Lesson]) -> tuple[list[Lesson], list[str]]:
    """Опечатка в годе внутри файла (07.09.2025 среди 2026) — правим на доминирующий год."""
    years = Counter(lesson.date.year for lesson in lessons)
    if len(years) < 2:
        return lessons, []
    dominant, count = years.most_common(1)[0]
    if count / sum(years.values()) < 0.7:
        return lessons, []

    wrong_days: dict[date, int] = {}
    result: list[Lesson] = []
    for lesson in lessons:
        if lesson.date.year != dominant and abs(lesson.date.year - dominant) == 1:
            try:
                corrected = lesson.date.replace(year=dominant)
            except ValueError:
                result.append(lesson)
                continue
            wrong_days[lesson.date] = wrong_days.get(lesson.date, 0) + 1
            result.append(lesson.with_(date=corrected))
        else:
            result.append(lesson)
    if not wrong_days:
        return result, []
    examples = ", ".join(
        f"{day:%d.%m.%Y}→{day.replace(year=dominant):%d.%m.%Y}"
        for day in sorted(wrong_days)[:3]
    )
    warnings = [f"исправлен год в датах: {examples} ({sum(wrong_days.values())} занятий)"]
    return result, warnings


def _normalize_subjects(lessons: list[Lesson]) -> tuple[list[Lesson], list[str]]:
    """Склеивает обрезанные в PDF названия: «микробиол» → «микробиолог» (самый длинный вариант)."""
    subjects = sorted({lesson.subject for lesson in lessons if lesson.subject})
    mapping: dict[str, str] = {}
    for short in subjects:
        if len(short) < 5:
            continue
        longer = [
            other
            for other in subjects
            if other != short
            and other.lower().startswith(short.lower())
            # обрыв внутри слова, а не «История» → «История России»
            and other[len(short) : len(short) + 1] not in {"", " "}
        ]
        if longer:
            mapping[short] = max(longer, key=len)
    if not mapping:
        return lessons, []
    fixed = [
        lesson.with_(subject=mapping[lesson.subject]) if lesson.subject in mapping else lesson
        for lesson in lessons
    ]
    examples = ", ".join(f"«{short}»" for short in sorted(mapping)[:3])
    return fixed, [f"обрезанные названия предметов объединены в PDF: {examples}"]


def _normalize_initials(raw: str) -> str:
    value = re.sub(r"\s+", "", raw)
    if re.fullmatch(r"[А-ЯЁ]", value):
        return value + "."
    if re.fullmatch(r"[А-ЯЁ]\.[А-ЯЁ]", value):
        return value + "."
    return value


def _is_noise(text: str) -> bool:
    return text.lower().strip(" .") in NOISE


def _group_sort_key(name: str) -> tuple:
    digits = re.match(r"(\d+)", name)
    return (int(digits.group(1)) if digits else 9999, name)


def parse_pdf(path: str | Path, **kwargs) -> ParseResult:
    return ScheduleParser(**kwargs).parse_file(path)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Разбор PDF расписания (проверка без бота)")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--json", action="store_true", help="вывести JSON")
    ap.add_argument("--year", type=int, default=None, help="год для дат без года")
    ap.add_argument("--group-regex", default=DEFAULT_GROUP_RE)
    args = ap.parse_args(argv)

    result = parse_pdf(args.pdf, group_regex=args.group_regex, default_year=args.year)
    if args.json:
        print(
            json.dumps(
                {
                    "lessons": [lesson.as_row() for lesson in result.lessons],
                    "groups": list(result.groups),
                    "dates": [d.isoformat() for d in result.dates],
                    "warnings": list(result.warnings),
                    "pages": result.pages,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"Файл: {args.pdf}  ·  страниц: {result.pages}")
    print(f"Занятий: {len(result.lessons)}  ·  групп: {len(result.groups)}  ·  дней: {len(result.dates)}")
    if result.dates:
        print(f"Период: {result.dates[0]:%d.%m.%Y} — {result.dates[-1]:%d.%m.%Y}")
    print(f"Группы: {', '.join(result.groups) or '—'}")
    for w in result.warnings:
        print(f"⚠ {w}")
    print()
    current = None
    for lesson in result.lessons:
        if lesson.date != current:
            current = lesson.date
            print(f"— {lesson.weekday}, {lesson.date:%d.%m.%Y} —")
        print(
            f"  {lesson.start}-{lesson.end or '??'}  {lesson.group:<5} "
            f"{lesson.subject or '—':<30} каб. {lesson.room or '—':<4} {lesson.teacher}"
        )
    return 0 if result.lessons else 1


if __name__ == "__main__":
    sys.exit(main())
