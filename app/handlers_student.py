"""Хендлеры для студентов."""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

from . import formatting as fmt
from .keyboards import groups_kb, main_kb, teachers_kb
from .models import Lesson
from .service import (
    Context,
    day_lessons,
    find_teachers,
    next_lesson,
    search,
    teacher_week,
    upcoming_week,
)

HELP_TEXT = (
    "<b>Что умеет бот</b>\n\n"
    "• «Сегодня», «Завтра», «Неделя» — расписание вашей группы\n"
    "• «Следующая пара» — что и когда дальше\n"
    "• «Другая группа» — сменить группу\n"
    "• «Преподаватель» — расписание преподавателя\n"
    "• «Звонки» — сетка звонков\n"
    "• «Экспорт .ics» — добавить в календарь телефона\n"
    "• «Напоминания» — включить/выключить уведомления об изменениях\n\n"
    "Поиск: напишите «информатика», «каб. 402» или фамилию — на фамилию бот покажет\n"
    "расписание этого преподавателя на неделю.\n"
    "В любом чате: <code>@бот 103С</code> — расписание этой группы."
)

ABOUT = (
    "Расписание берётся из PDF учебной части: админ загружает файл, бот разбирает "
    "таблицу и рассылает изменения подписчикам."
)


def build_router(ctx: Context) -> Router:
    store, config, tz = ctx.store, ctx.config, ctx.tz
    router = Router(name="student")

    # ------------------------------------------------------------------ helpers
    def menu(user_id: int):
        return main_kb(config.is_admin(user_id))

    async def ask_group(message: Message, text: str = "Выберите группу:") -> None:
        groups = store.groups()
        if not groups:
            await message.answer(
                "Расписание ещё не загружено. Как только учебная часть пришлёт PDF, "
                "оно появится здесь.",
                reply_markup=menu(message.from_user.id),
            )
            return
        await message.answer(text, reply_markup=groups_kb(groups))

    async def show_today(message: Message, group: str, *, tomorrow: bool = False) -> None:
        day = ctx.now().date() + timedelta(days=1 if tomorrow else 0)
        lessons = day_lessons(store, group, day)
        await message.answer(fmt.render_day(group, day, lessons), reply_markup=menu(message.from_user.id))

    async def show_week(message: Message, group: str) -> None:
        monday, sunday, lessons = upcoming_week(store, group, ctx.now().date())
        for text in fmt.render_week(group, lessons):
            await message.answer(text, reply_markup=menu(message.from_user.id))

    async def show_next(message: Message, group: str) -> None:
        lesson = next_lesson(store, group, ctx.now())
        if lesson is None:
            await message.answer(
                f"📅 <b>{fmt.esc(group)}</b>\n\nВ ближайшие три недели пар нет.",
                reply_markup=menu(message.from_user.id),
            )
            return
        now = ctx.now()
        head = "▶️ <b>Сейчас идёт</b>" if lesson.begins_at(tz) <= now else (
            "⏭ <b>Следующая пара</b> " + fmt.relative_when(lesson.begins_at(tz) - now)
        )
        await message.answer(
            f"{head} · <b>{fmt.esc(group)}</b>\n\n"
            f"{fmt.lesson_line(1, lesson)}\n\n"
            f"<i>{lesson.weekday}, {lesson.date:%d.%m.%Y}</i>",
            reply_markup=menu(message.from_user.id),
        )

    async def user_group(message: Message) -> str | None:
        user = store.user(message.from_user.id)
        if user and user.group:
            groups = store.groups()
            if not groups or user.group in groups:
                return user.group
        return None

    # ------------------------------------------------------------------ команды
    @router.message(CommandStart())
    async def cmd_start(message: Message, command: CommandObject) -> None:
        store.upsert_user(message.from_user.id)
        group = (command.args or "").strip().upper() if command.args else ""
        groups = store.groups()
        if group and group in groups:
            store.set_group(message.from_user.id, group)
            await message.answer(
                f"Готово: ваша группа <b>{fmt.esc(group)}</b>.", reply_markup=menu(message.from_user.id)
            )
            await show_today(message, group)
            return
        if not groups:
            await message.answer(
                "Привет! Это бот расписания.\n\nРасписание пока не загружено — "
                "как только учебная часть пришлёт PDF, пары появятся здесь.",
                reply_markup=menu(message.from_user.id),
            )
            return
        await message.answer(
            "Привет! Я показываю расписание занятий.\n\nВыберите свою группу — "
            "запомню её и буду присылать изменения:",
            reply_markup=groups_kb(groups),
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(HELP_TEXT, reply_markup=menu(message.from_user.id))

    @router.message(Command("id"))
    async def cmd_id(message: Message) -> None:
        await message.answer(
            f"chat_id: <code>{message.chat.id}</code>\nuser_id: <code>{message.from_user.id}</code>"
        )

    @router.message(Command("group"))
    async def cmd_group(message: Message) -> None:
        await ask_group(message)

    @router.message(Command("today"))
    async def cmd_today(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await show_today(message, group)

    @router.message(Command("tomorrow"))
    async def cmd_tomorrow(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await show_today(message, group, tomorrow=True)

    @router.message(Command("week"))
    async def cmd_week(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await show_week(message, group)

    @router.message(Command("next"))
    async def cmd_next(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await show_next(message, group)

    @router.message(Command("teacher"))
    async def cmd_teacher(message: Message, command: CommandObject) -> None:
        teachers = store.teachers()
        if not teachers:
            await message.answer("Расписание ещё не загружено.")
            return
        query = (command.args or "").strip().lower()
        if query:
            await send_teacher_week(message, query)
            return
        await message.answer("Выберите преподавателя:", reply_markup=teachers_kb(teachers))

    @router.message(Command("ics"))
    async def cmd_ics(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await send_ics(message, group)

    @router.message(Command("bells"))
    async def cmd_bells(message: Message) -> None:
        await message.answer(fmt.render_bells(store.lessons()))

    # ------------------------------------------------------------------ кнопки
    @router.message(F.text == "Сегодня")
    async def btn_today(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await show_today(message, group)

    @router.message(F.text == "Завтра")
    async def btn_tomorrow(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await show_today(message, group, tomorrow=True)

    @router.message(F.text == "Неделя")
    async def btn_week(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await show_week(message, group)

    @router.message(F.text == "Следующая пара")
    async def btn_next(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await show_next(message, group)

    @router.message(F.text == "Другая группа")
    async def btn_group(message: Message) -> None:
        await ask_group(message, "Выберите группу — расписание и уведомления переключатся на неё:")

    @router.message(F.text == "Преподаватель")
    async def btn_teacher(message: Message) -> None:
        teachers = store.teachers()
        if not teachers:
            await message.answer("Расписание ещё не загружено.")
            return
        await message.answer("Выберите преподавателя:", reply_markup=teachers_kb(teachers))

    @router.message(F.text == "Звонки")
    async def btn_bells(message: Message) -> None:
        await message.answer(fmt.render_bells(store.lessons()))

    @router.message(F.text == "Экспорт .ics")
    async def btn_ics(message: Message) -> None:
        group = await user_group(message)
        if not group:
            await ask_group(message)
            return
        await send_ics(message, group)

    @router.message(F.text == "Напоминания")
    async def btn_notify(message: Message) -> None:
        user = store.upsert_user(message.from_user.id)
        user = store.toggle(message.from_user.id, "notify")
        state = "включены ✅" if user.notify else "выключены 🔕"
        await message.answer(
            f"Уведомления об изменениях расписания {state}.\n"
            "Они приходят, когда админ публикует новый PDF с заменами.",
            reply_markup=menu(message.from_user.id),
        )

    @router.message(F.text == "Помощь")
    async def btn_help(message: Message) -> None:
        await message.answer(HELP_TEXT, reply_markup=menu(message.from_user.id))

    # ------------------------------------------------------------------ callbacks
    @router.callback_query(F.data.startswith("setg:"))
    async def cb_set_group(callback: CallbackQuery) -> None:
        group = callback.data.split(":", 1)[1]
        if group not in store.groups():
            await callback.answer("Такой группы нет в расписании", show_alert=True)
            return
        store.set_group(callback.from_user.id, group)
        await callback.answer(f"Группа: {group}")
        if callback.message:
            day = ctx.now().date()
            await callback.message.edit_text(
                fmt.render_day(group, day, day_lessons(store, group, day))
            )
            await callback.message.answer("Готово! Дальше — кнопками ниже.", reply_markup=menu(callback.from_user.id))

    @router.callback_query(F.data.startswith("grp:"))
    async def cb_group_page(callback: CallbackQuery) -> None:
        page = int(callback.data.split(":", 1)[1] or 0)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=groups_kb(store.groups(), page))
        await callback.answer()

    @router.callback_query(F.data.startswith("tp:"))
    async def cb_teacher_page(callback: CallbackQuery) -> None:
        page = int(callback.data.split(":", 1)[1] or 0)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=teachers_kb(store.teachers(), page))
        await callback.answer()

    @router.callback_query(F.data.startswith("sett:"))
    async def cb_teacher(callback: CallbackQuery) -> None:
        teacher = callback.data.split(":", 1)[1]
        store.upsert_user(callback.from_user.id, teacher=teacher)
        await callback.answer(f"Преподаватель: {teacher}")
        if callback.message:
            monday, sunday, lessons = teacher_week(store, teacher, ctx.now().date())
            text = _render_teacher_week(teacher, lessons)
            await callback.message.edit_text(text)

    @router.callback_query(F.data == "noop")
    async def cb_noop(callback: CallbackQuery) -> None:
        await callback.answer()

    # ------------------------------------------------------------------ поиск
    @router.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message) -> None:
        text = (message.text or "").strip()
        groups = store.groups()
        if text.upper() in groups:
            store.set_group(message.from_user.id, text.upper())
            await message.answer(f"Группа: <b>{fmt.esc(text.upper())}</b>", reply_markup=menu(message.from_user.id))
            await show_today(message, text.upper())
            return
        if len(text) < 2:
            await message.answer("Напишите хотя бы два символа — или выберите действие кнопкой.")
            return
        # «Шамухаммедов» → личное расписание преподавателя, а не список совпадений
        found_teachers = find_teachers(store, text)
        if len(found_teachers) == 1:
            teacher = found_teachers[0]
            store.upsert_user(message.from_user.id, teacher=teacher)
            monday, sunday, lessons = teacher_week(store, teacher, ctx.now().date())
            await message.answer(
                _render_teacher_week(teacher, lessons), reply_markup=menu(message.from_user.id)
            )
            return
        if len(found_teachers) > 1:
            await message.answer(
                f"Нашёл {len(found_teachers)} преподавателей — уточните фамилию:",
                reply_markup=teachers_kb(found_teachers),
            )
            return
        found = search(store, text)
        await message.answer(fmt.render_search(text, found), reply_markup=menu(message.from_user.id))

    # ------------------------------------------------------------------ inline
    @router.inline_query()
    async def on_inline(query: InlineQuery) -> None:
        needle = query.query.strip().upper()
        groups = store.groups()
        results: list[InlineQueryResultArticle] = []
        for group in groups:
            if needle and needle not in group.upper():
                continue
            monday, sunday, lessons = upcoming_week(store, group, ctx.now().date())
            text = "\n".join(fmt.render_week(group, lessons))[:4000]
            results.append(
                InlineQueryResultArticle(
                    id=group,
                    title=f"Расписание {group}",
                    description=f"{len(lessons)} пар на неделе",
                    input_message_content=InputTextMessageContent(
                        message_text=text, parse_mode="HTML"
                    ),
                )
            )
        await query.answer(results[:20], cache_time=60, is_personal=True)

    # ------------------------------------------------------------------ helpers
    async def send_ics(message: Message, group: str) -> None:
        lessons = store.lessons_for_group(group)
        if not lessons:
            await message.answer("Пока нет занятий для экспорта.")
            return
        data = fmt.to_ics(lessons, tz, calendar_name=f"Расписание {group}")
        await message.answer_document(
            BufferedInputFile(data.encode("utf-8"), filename=f"raspisanie-{group}.ics"),
            caption=f"📎 {fmt.esc(group)} · {len(lessons)} занятий",
        )

    async def send_teacher_week(message: Message, query: str) -> None:
        teachers = store.teachers()
        matches = [t for t in teachers if query in t.lower()]
        if not matches:
            await message.answer("Преподаватель не найден. Попробуйте фамилию.")
            return
        if len(matches) > 1:
            await message.answer("Уточните:", reply_markup=teachers_kb(matches))
            return
        teacher = matches[0]
        store.upsert_user(message.from_user.id, teacher=teacher)
        monday, sunday, lessons = teacher_week(store, teacher, ctx.now().date())
        await message.answer(_render_teacher_week(teacher, lessons), reply_markup=menu(message.from_user.id))

    def _render_teacher_week(teacher: str, lessons: list[Lesson]) -> str:
        if not lessons:
            return f"👤 <b>{fmt.esc(teacher)}</b>\n\nНа этой неделе пар нет."
        by_day: dict[date, list[Lesson]] = {}
        for lesson in lessons:
            by_day.setdefault(lesson.date, []).append(lesson)
        lines = [f"👤 <b>{fmt.esc(teacher)}</b> · неделя\n"]
        for day in sorted(by_day):
            lines.append(f"<b>{fmt.day_title(day)}</b>")
            for lesson in by_day[day]:
                lines.append(
                    f"  <code>{lesson.time_range()}</code> {fmt.esc(lesson.group)} · "
                    f"{fmt.esc(lesson.subject or '—')}"
                    + (f" · каб. {fmt.esc(lesson.room)}" if lesson.room else "")
                )
            lines.append("")
        return "\n".join(lines)

    return router
