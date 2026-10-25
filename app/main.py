"""Точка входа: long polling + утренний дайджест.

    .venv/bin/python -m app.main            # запустить бота
    .venv/bin/python -m app.main --check    # проверить токен и выйти
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import Config
from .handlers_admin import build_router as build_admin_router
from .handlers_student import build_router as build_student_router
from .service import Context, day_lessons
from .store import Store

log = logging.getLogger("schedule-bot")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def digest_loop(bot: Bot, ctx: Context) -> None:
    """Раз в минуту проверяет, не пора ли разослать утренний дайджест."""
    store, config = ctx.store, ctx.config
    while True:
        try:
            now = ctx.now()
            if now.strftime("%H:%M") >= config.digest_time:
                marker = f"digest:{now.date().isoformat()}"
                if store.get_meta("digest_sent") != marker:
                    sent = 0
                    for user in store.subscribers():
                        if not user.digest or not user.group:
                            continue
                        lessons = day_lessons(store, user.group, now.date())
                        if not lessons:
                            continue
                        from . import formatting as fmt

                        text = "☀️ <b>Доброе утро!</b>\n\n" + fmt.render_day(
                            user.group, now.date(), lessons
                        )
                        try:
                            await bot.send_message(user.tg_id, text)
                            sent += 1
                        except Exception as exc:  # noqa: BLE001
                            log.warning("дайджест %s: %s", user.tg_id, exc)
                    store.set_meta("digest_sent", marker)
                    if sent:
                        log.info("дайджест отправлен %s пользователям", sent)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("ошибка в цикле дайджеста")
        await asyncio.sleep(60)


async def start_health_server():
    """Для облачных площадок (Render/Koyeb/HF): отдаём 200 на /healthz и держим порт.

    Локально (PORT не задан) ничего не поднимаем — работает только long polling.
    """
    port = os.environ.get("PORT")
    if not port:
        return None
    from aiohttp import web

    async def health(_request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(port))
    await site.start()
    log.info("health-сервер слушает :%s", port)
    return runner


async def check(config: Config) -> int:
    bot = Bot(token=config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        me = await bot.get_me()
        print(f"✅ Токен рабочий: @{me.username} ({me.full_name}), id={me.id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Не удалось подключиться к Telegram: {exc}")
        return 1
    finally:
        await bot.session.close()


async def run(config: Config) -> None:
    store = Store(config.db_path)
    ctx = Context(store=store, config=config)
    bot = Bot(token=config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_admin_router(ctx))
    dp.include_router(build_student_router(ctx))

    me = await bot.get_me()
    log.info("бот @%s запущен, БД: %s", me.username, config.db_path)
    if not config.admin_ids:
        log.warning("ADMIN_IDS пуст — загрузка PDF будет недоступна")

    digest_task = asyncio.create_task(digest_loop(bot, ctx))
    health = await start_health_server()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        digest_task.cancel()
        if health is not None:
            await health.cleanup()
        await bot.session.close()
        store.close()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    config = Config.load()
    if not config.is_configured:
        print(
            "Токен не найден.\n\n"
            "1. Получите токен у @BotFather (если он засветился — /revoke и новый).\n"
            f"2. Скопируйте .env.example в .env и впишите BOT_TOKEN и ADMIN_IDS.\n"
            f"   Файл: {config.db_path.parent.parent / '.env'}"
        )
        return 1
    if "--check" in argv:
        return asyncio.run(check(config))
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        print("\nОстановлено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
