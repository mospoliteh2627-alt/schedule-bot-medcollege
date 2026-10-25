"""Админские хендлеры: загрузка PDF, предпросмотр, публикация, откат."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from . import formatting as fmt
from .importer import ImportPreview, prepare, publish
from .keyboards import (
    admin_kb,
    confirm_rollback_kb,
    import_kb,
    main_kb,
    versions_kb,
)
from .service import Context

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 20 * 1024 * 1024


class AdminStates(StatesGroup):
    waiting_pdf = State()


def build_router(ctx: Context) -> Router:
    store, config = ctx.store, ctx.config
    pending_dir = config.data_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    router = Router(name="admin")

    def is_admin(user_id: int | None) -> bool:
        return config.is_admin(user_id)

    async def deny(message: Message) -> None:
        await message.answer(
            "Это админская команда. Ваш user_id: "
            f"<code>{message.from_user.id}</code> — добавьте его в ADMIN_IDS в .env."
        )

    async def show_admin(message: Message) -> None:
        stats = store.stats()
        period = ctx.store.dates()
        text = (
            "🛠 <b>Админка</b>\n\n"
            f"Занятий: {stats['lessons']} · групп: {stats['groups']} · "
            f"преподавателей: {stats['teachers']}\n"
            f"Пользователей: {stats['users']} (с уведомлениями: {stats['subscribers']})\n"
            f"Версий расписания: {stats['sources']}"
        )
        if period:
            text += f"\nПериод: {period[0]:%d.%m.%Y} — {period[-1]:%d.%m.%Y}"
        await message.answer(text, reply_markup=admin_kb())

    # ------------------------------------------------------------------ вход
    @router.message(Command("admin"))
    async def cmd_admin(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            await deny(message)
            return
        await state.clear()
        await show_admin(message)

    @router.message(F.text == "Админка")
    async def btn_admin(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            await deny(message)
            return
        await state.clear()
        await show_admin(message)

    # ------------------------------------------------------------------ меню
    @router.callback_query(F.data == "adm:upload")
    async def cb_upload(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_pdf)
        await state.update_data(check_only=False)
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "Пришлите PDF расписания файлом (не фото и не текст).\n\n"
                "• обычный PDF — заменяет даты, которые есть в файле;\n"
                "• перед публикацией покажу, что изменится.\n\n"
                "Отмена: /cancel"
            )

    @router.callback_query(F.data == "adm:check")
    async def cb_check(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_pdf)
        await state.update_data(check_only=True)
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "🧪 Пришлите PDF — разберу и покажу результат, но ничего не опубликую."
            )

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_kb(is_admin(message.from_user.id)))

    # ------------------------------------------------------------------ приём файла
    @router.message(StateFilter(AdminStates.waiting_pdf), F.document)
    async def on_document(message: Message, state: FSMContext, bot: Bot) -> None:
        if not is_admin(message.from_user.id):
            await deny(message)
            return
        document = message.document
        filename = document.file_name or "schedule.pdf"
        if not filename.lower().endswith(".pdf"):
            await message.answer("Нужен именно PDF. Excel/сканы пока не поддерживаются — сохраните как PDF.")
            return
        if (document.file_size or 0) > MAX_PDF_BYTES:
            await message.answer("Файл больше 20 МБ — пришлите, пожалуйста, поменьше.")
            return

        data = await state.get_data()
        check_only = bool(data.get("check_only"))
        mode = data.get("mode", "dates")

        await message.answer("⏳ Разбираю PDF…")
        buffer = await bot.download(document)
        payload = buffer.read()

        path = pending_dir / f"{uuid.uuid4().hex}.pdf"
        path.write_bytes(payload)
        try:
            preview = prepare(
                store,
                payload,
                filename,
                mode=mode,
                default_year=config.default_year,
            )
        except Exception as exc:  # noqa: BLE001 — показываем админу причину
            log.exception("не удалось разобрать PDF")
            path.unlink(missing_ok=True)
            await message.answer(f"❌ Не смог разобрать PDF: <code>{fmt.esc(str(exc))}</code>")
            return

        if not preview.ok:
            path.unlink(missing_ok=True)
            text = "❌ Разбор не дал занятий — публиковать нечего.\n\n" + fmt.render_import_preview(preview)
            await message.answer(text, reply_markup=admin_kb())
            await state.clear()
            return

        await state.update_data(file=str(path), mode=mode, check_only=check_only, filename=filename)
        text = fmt.render_import_preview(preview)
        if check_only:
            text += "\n\n🧪 Проверка: ничего не опубликовано."
            await message.answer(text, reply_markup=admin_kb())
            await state.clear()
            path.unlink(missing_ok=True)
            return
        await message.answer(text, reply_markup=import_kb(mode, has_changes=not preview.diff.is_empty))

    # ------------------------------------------------------------------ предпросмотр
    async def _pending_preview(state: FSMContext) -> tuple[ImportPreview | None, Path | None, bool]:
        data = await state.get_data()
        raw_path = data.get("file")
        if not raw_path:
            return None, None, bool(data.get("check_only"))
        path = Path(raw_path)
        if not path.exists():
            return None, None, bool(data.get("check_only"))
        preview = prepare(
            store,
            path.read_bytes(),
            data.get("filename", path.name),
            mode=data.get("mode", "dates"),
            default_year=config.default_year,
        )
        return preview, path, bool(data.get("check_only"))

    @router.callback_query(F.data == "imp:mode")
    async def cb_mode(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        data = await state.get_data()
        mode = "all" if data.get("mode", "dates") == "dates" else "dates"
        await state.update_data(mode=mode)
        preview, _path, _ = await _pending_preview(state)
        if preview is None:
            await callback.answer("Файл не найден — пришлите PDF заново", show_alert=True)
            await state.clear()
            return
        await callback.answer("Режим переключён")
        if callback.message:
            await callback.message.edit_text(
                fmt.render_import_preview(preview),
                reply_markup=import_kb(mode, has_changes=not preview.diff.is_empty),
            )

    @router.callback_query(F.data == "imp:cancel")
    async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        raw_path = data.get("file")
        if raw_path:
            Path(raw_path).unlink(missing_ok=True)
        await state.clear()
        await callback.answer("Отменено")
        if callback.message:
            await callback.message.edit_text("Импорт отменён. Расписание не изменилось.")

    @router.callback_query(F.data == "imp:publish")
    async def cb_publish(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        preview, path, _ = await _pending_preview(state)
        if preview is None or path is None:
            await callback.answer("Файл не найден — пришлите PDF заново", show_alert=True)
            await state.clear()
            return
        if not preview.ok:
            await callback.answer("В файле нет занятий", show_alert=True)
            return

        diff = preview.diff
        try:
            source_id = publish(store, preview, user_id=callback.from_user.id)
        except Exception as exc:  # noqa: BLE001
            log.exception("ошибка публикации")
            await callback.answer("Ошибка публикации", show_alert=True)
            if callback.message:
                await callback.message.answer(f"❌ Не удалось опубликовать: <code>{fmt.esc(str(exc))}</code>")
            return
        path.unlink(missing_ok=True)
        await state.clear()

        notified = await notify_changes(bot, diff) if not diff.is_empty else 0
        text = fmt.render_import_done(preview, source_id, notified)
        if callback.message:
            await callback.message.edit_text(text)
            await callback.message.answer("Готово. Продолжаем.", reply_markup=main_kb(True))
        await callback.answer("Опубликовано")

    async def notify_changes(bot: Bot, diff) -> int:
        sent = 0
        for group in diff.groups():
            text = fmt.render_changes(diff, group)
            if not text:
                continue
            for user in store.subscribers(group):
                try:
                    await bot.send_message(user.tg_id, text)
                    sent += 1
                except Exception as exc:  # noqa: BLE001 — заблокировал бот и т.п.
                    log.warning("не смог отправить %s: %s", user.tg_id, exc)
        return sent

    # ------------------------------------------------------------------ версии и статистика
    @router.callback_query(F.data == "adm:versions")
    async def cb_versions(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        sources = store.sources(limit=8)
        if not sources:
            await callback.answer("Версий пока нет", show_alert=True)
            return
        lines = ["🕓 <b>Версии расписания</b>\n"]
        for s in sources:
            period = ""
            if s.dates:
                period = f" · {s.dates[0]:%d.%m}–{s.dates[-1]:%d.%m}"
            lines.append(
                f"#{s.id} · {s.imported_at[:16].replace('T', ' ')} · "
                f"{s.lessons_count} пар{period} · {fmt.esc(s.name)}"
            )
        if callback.message:
            await callback.message.answer(
                "\n".join(lines), reply_markup=versions_kb(sources)
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("adm:rollback:"))
    async def cb_rollback(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        source_id = int(callback.data.rsplit(":", 1)[1])
        if callback.message:
            await callback.message.answer(
                f"Откатить расписание к версии #{source_id}?", reply_markup=confirm_rollback_kb(source_id)
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("adm:dorollback:"))
    async def cb_dorollback(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        source_id = int(callback.data.rsplit(":", 1)[1])
        try:
            new_id = store.rollback(source_id, user_id=callback.from_user.id)
        except KeyError:
            await callback.answer("Версия не найдена", show_alert=True)
            return
        await callback.answer("Готово")
        if callback.message:
            await callback.message.edit_text(
                f"↩️ Откат выполнен: версия #{source_id} → новая версия #{new_id}."
            )

    @router.callback_query(F.data == "adm:stats")
    async def cb_stats(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        stats = store.stats()
        by_group = {}
        for lesson in store.lessons():
            by_group[lesson.group] = by_group.get(lesson.group, 0) + 1
        lines = [
            "📊 <b>Статистика</b>\n",
            f"Занятий: {stats['lessons']}",
            f"Групп: {stats['groups']}",
            f"Преподавателей: {stats['teachers']}",
            f"Пользователей: {stats['users']} (подписаны: {stats['subscribers']})",
            "\n<b>По группам</b>",
        ]
        for group in sorted(by_group):
            lines.append(f"  {group}: {by_group[group]}")
        if callback.message:
            await callback.message.answer("\n".join(lines))
        await callback.answer()

    @router.callback_query(F.data == "adm:cancel")
    async def cb_admin_cancel(callback: CallbackQuery) -> None:
        await callback.answer("Отменено")
        if callback.message:
            await callback.message.edit_text("Отменено.")

    return router
