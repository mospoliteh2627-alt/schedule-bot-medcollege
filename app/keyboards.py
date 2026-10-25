"""Клавиатуры."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

GROUPS_PER_PAGE = 12
TEACHERS_PER_PAGE = 10


def groups_kb(groups: list[str], page: int = 0, prefix: str = "setg") -> InlineKeyboardMarkup:
    pages = max(1, (len(groups) + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = groups[page * GROUPS_PER_PAGE : (page + 1) * GROUPS_PER_PAGE]
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(chunk), 3):
        rows.append(
            [InlineKeyboardButton(text=g, callback_data=f"{prefix}:{g}") for g in chunk[i : i + 3]]
        )
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="←", callback_data=f"grp:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="→", callback_data=f"grp:{page + 1}"))
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def teachers_kb(teachers: list[str], page: int = 0) -> InlineKeyboardMarkup:
    pages = max(1, (len(teachers) + TEACHERS_PER_PAGE - 1) // TEACHERS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = teachers[page * TEACHERS_PER_PAGE : (page + 1) * TEACHERS_PER_PAGE]
    rows = [[InlineKeyboardButton(text=t, callback_data=f"sett:{t}")] for t in chunk]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="←", callback_data=f"tp:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="→", callback_data=f"tp:{page + 1}"))
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")],
        [KeyboardButton(text="Неделя"), KeyboardButton(text="Следующая пара")],
        [KeyboardButton(text="Другая группа"), KeyboardButton(text="Преподаватель")],
        [KeyboardButton(text="Звонки"), KeyboardButton(text="Экспорт .ics")],
        [KeyboardButton(text="Напоминания"), KeyboardButton(text="Помощь")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="Админка")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def import_kb(mode: str = "dates", has_changes: bool = True) -> InlineKeyboardMarkup:
    mode_label = (
        "Режим: только даты из файла ✅" if mode == "dates" else "Режим: полная замена ⚠️"
    )
    rows = [[InlineKeyboardButton(text=mode_label, callback_data="imp:mode")]]
    if has_changes:
        rows.append(
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data="imp:publish"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="imp:cancel"),
            ]
        )
    else:
        rows.append([InlineKeyboardButton(text="Закрыть", callback_data="imp:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Загрузить PDF", callback_data="adm:upload")],
            [
                InlineKeyboardButton(text="🕓 Версии", callback_data="adm:versions"),
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
            ],
            [InlineKeyboardButton(text="🧪 Проверить PDF без публикации", callback_data="adm:check")],
        ]
    )


def versions_kb(sources) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"↩️ #{s.id} · {s.imported_at[:16].replace('T', ' ')} · {s.lessons_count} пар",
                callback_data=f"adm:rollback:{s.id}",
            )
        ]
        for s in sources
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(text="Пусто", callback_data="noop")]])


def confirm_rollback_kb(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, откатить", callback_data=f"adm:dorollback:{source_id}"),
                InlineKeyboardButton(text="Нет", callback_data="adm:cancel"),
            ]
        ]
    )
