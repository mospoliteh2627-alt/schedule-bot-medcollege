"""Конфигурация бота расписания.

Токен читается только из окружения / .env и никогда не пишется в код,
логи и git. См. .env.example.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_DIR / "data" / "schedule.sqlite3"
DEFAULT_GROUP_RE = r"^\d{3}[СЛП]$"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _ints(raw: str) -> set[int]:
    out: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return out


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    admin_ids: set[int] = field(default_factory=set)
    db_path: Path = DEFAULT_DB
    timezone: str = "Europe/Moscow"
    college_name: str = "Медколледж"
    group_regex: str = DEFAULT_GROUP_RE
    default_year: int = field(default_factory=lambda: date.today().year)
    digest_time: str = "07:30"
    reminder_minutes: int = 0  # 0 = напоминания выключены
    data_dir: Path = PROJECT_DIR / "data"

    @property
    def is_configured(self) -> bool:
        return bool(self.token and ":" in self.token)

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids

    @classmethod
    def load(cls, env_path: Path | None = None) -> Config:
        env_path = env_path or PROJECT_DIR / ".env"
        file_values = _parse_env_file(env_path)

        def get(name: str, default: str = "") -> str:
            return os.environ.get(name) or file_values.get(name) or default

        year_raw = get("DEFAULT_YEAR")
        year = int(year_raw) if year_raw.isdigit() else date.today().year
        db_raw = get("DB_PATH")
        db_path = Path(db_raw).expanduser() if db_raw else DEFAULT_DB
        if not db_path.is_absolute():
            db_path = (PROJECT_DIR / db_path).resolve()

        return cls(
            token=get("BOT_TOKEN").strip(),
            admin_ids=_ints(get("ADMIN_IDS")),
            db_path=db_path,
            timezone=get("TZ", "Europe/Moscow") or "Europe/Moscow",
            college_name=get("COLLEGE_NAME", "Медколледж") or "Медколледж",
            group_regex=get("GROUP_REGEX", DEFAULT_GROUP_RE) or DEFAULT_GROUP_RE,
            default_year=year,
            digest_time=get("DIGEST_TIME", "07:30") or "07:30",
            reminder_minutes=int(get("REMINDER_MINUTES", "0") or 0),
            data_dir=(PROJECT_DIR / "data"),
        )
