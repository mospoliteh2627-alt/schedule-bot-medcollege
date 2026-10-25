"""Хранилище расписания: SQLite, снапшоты версий, пользователи."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .models import Lesson

_UNSET = object()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    filename      TEXT,
    sha256        TEXT,
    imported_at   TEXT NOT NULL,
    lessons_count INTEGER NOT NULL DEFAULT 0,
    dates         TEXT NOT NULL DEFAULT '[]',
    groups        TEXT NOT NULL DEFAULT '[]',
    mode          TEXT NOT NULL DEFAULT 'dates',
    lessons       TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS lessons (
    date       TEXT NOT NULL,
    start      TEXT NOT NULL,
    end        TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL,
    subject    TEXT NOT NULL DEFAULT '',
    room       TEXT NOT NULL DEFAULT '',
    teacher    TEXT NOT NULL DEFAULT '',
    subgroup   TEXT NOT NULL DEFAULT '',
    source_id  INTEGER,
    PRIMARY KEY (date, start, group_name, subgroup)
);

CREATE INDEX IF NOT EXISTS lessons_by_group ON lessons (group_name, date, start);
CREATE INDEX IF NOT EXISTS lessons_by_teacher ON lessons (teacher, date, start);
CREATE INDEX IF NOT EXISTS lessons_by_date ON lessons (date);

CREATE TABLE IF NOT EXISTS users (
    tg_id      INTEGER PRIMARY KEY,
    group_name TEXT,
    teacher    TEXT,
    notify     INTEGER NOT NULL DEFAULT 1,
    digest     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_seen  TEXT
);

CREATE TABLE IF NOT EXISTS journal (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    user_id INTEGER,
    action  TEXT NOT NULL,
    detail  TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class User:
    tg_id: int
    group: str | None = None
    teacher: str | None = None
    notify: bool = True
    digest: bool = True


@dataclass(frozen=True, slots=True)
class SourceInfo:
    id: int
    name: str
    filename: str
    imported_at: str
    lessons_count: int
    dates: tuple[date, ...]
    groups: tuple[str, ...]
    mode: str


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ служебное
    def log(self, action: str, detail: str = "", user_id: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO journal (at, user_id, action, detail) VALUES (?,?,?,?)",
            (_now(), user_id, action, detail),
        )
        self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ------------------------------------------------------------------ занятия
    def lessons(self) -> list[Lesson]:
        rows = self.conn.execute(
            "SELECT date, start, end, group_name, subject, room, teacher, subgroup "
            "FROM lessons ORDER BY date, start, group_name, subgroup"
        ).fetchall()
        return [_lesson_from_db(r) for r in rows]

    def lessons_for_group(self, group: str, date_from: date | None = None, date_to: date | None = None) -> list[Lesson]:
        return self._filtered("group_name = ?", group, date_from, date_to)

    def lessons_for_teacher(self, teacher: str, date_from: date | None = None, date_to: date | None = None) -> list[Lesson]:
        return self._filtered("teacher LIKE ?", f"%{teacher}%", date_from, date_to)

    def _filtered(
        self, where: str, param: str, date_from: date | None, date_to: date | None
    ) -> list[Lesson]:
        sql = (
            "SELECT date, start, end, group_name, subject, room, teacher, subgroup FROM lessons WHERE "
            + where
        )
        params: list = [param]
        if date_from:
            sql += " AND date >= ?"
            params.append(date_from.isoformat())
        if date_to:
            sql += " AND date <= ?"
            params.append(date_to.isoformat())
        sql += " ORDER BY date, start, group_name, subgroup"
        return [_lesson_from_db(r) for r in self.conn.execute(sql, params).fetchall()]

    def groups(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT group_name FROM lessons ORDER BY group_name"
        ).fetchall()
        return [r["group_name"] for r in rows]

    def teachers(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT teacher FROM lessons WHERE teacher <> '' ORDER BY teacher"
        ).fetchall()
        return [r["teacher"] for r in rows]

    def dates(self) -> list[date]:
        rows = self.conn.execute("SELECT DISTINCT date FROM lessons ORDER BY date").fetchall()
        return [date.fromisoformat(r["date"]) for r in rows]

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS c FROM lessons").fetchone()["c"])

    # ------------------------------------------------------------------ публикация
    def publish(
        self,
        lessons: Iterable[Lesson],
        *,
        scope_dates: Iterable[date],
        mode: str,
        name: str,
        filename: str = "",
        sha256: str = "",
        user_id: int | None = None,
    ) -> int:
        """Пишет занятия. mode='dates' — заменяет только даты из файла (замены),
        mode='all' — полностью заменяет расписание."""
        lessons = list(lessons)
        scope = sorted({d.isoformat() for d in scope_dates})
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute(
                "INSERT INTO sources (name, filename, sha256, imported_at, lessons_count, dates, groups, mode, lessons) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    name,
                    filename,
                    sha256,
                    _now(),
                    len(lessons),
                    json.dumps(scope, ensure_ascii=False),
                    json.dumps(sorted({lesson.group for lesson in lessons}), ensure_ascii=False),
                    mode,
                    json.dumps([lesson.as_row() for lesson in lessons], ensure_ascii=False),
                ),
            )
            source_id = int(cur.lastrowid)
            if mode == "all":
                cur.execute("DELETE FROM lessons")
            elif scope:
                cur.executemany("DELETE FROM lessons WHERE date = ?", [(d,) for d in scope])
            cur.executemany(
                "INSERT OR REPLACE INTO lessons "
                "(date, start, end, group_name, subject, room, teacher, subgroup, source_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        lesson.date.isoformat(),
                        lesson.start,
                        lesson.end,
                        lesson.group,
                        lesson.subject,
                        lesson.room,
                        lesson.teacher,
                        lesson.subgroup,
                        source_id,
                    )
                    for lesson in lessons
                ],
            )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        self.log("publish", f"source={source_id} lessons={len(lessons)} mode={mode}", user_id)
        return source_id

    def rollback(self, source_id: int, user_id: int | None = None) -> int:
        row = self.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise KeyError(f"версия {source_id} не найдена")
        lessons = [Lesson.from_row(r) for r in json.loads(row["lessons"])]
        mode = row["mode"]
        scope = [date.fromisoformat(d) for d in json.loads(row["dates"])]
        new_id = self.publish(
            lessons,
            scope_dates=scope,
            mode=mode,
            name=f"откат к #{source_id}",
            filename=row["filename"] or "",
            user_id=user_id,
        )
        return new_id

    def sources(self, limit: int = 10) -> list[SourceInfo]:
        rows = self.conn.execute(
            "SELECT id, name, filename, imported_at, lessons_count, dates, groups, mode "
            "FROM sources ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SourceInfo(
                id=r["id"],
                name=r["name"],
                filename=r["filename"] or "",
                imported_at=r["imported_at"],
                lessons_count=r["lessons_count"],
                dates=tuple(date.fromisoformat(d) for d in json.loads(r["dates"])),
                groups=tuple(json.loads(r["groups"])),
                mode=r["mode"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ пользователи
    def user(self, tg_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if not row:
            return None
        return User(
            tg_id=row["tg_id"],
            group=row["group_name"],
            teacher=row["teacher"],
            notify=bool(row["notify"]),
            digest=bool(row["digest"]),
        )

    def upsert_user(
        self,
        tg_id: int,
        *,
        group: str | None | object = _UNSET,
        teacher: str | None | object = _UNSET,
        notify: bool | object = _UNSET,
        digest: bool | object = _UNSET,
    ) -> User:
        now = _now()
        self.conn.execute(
            "INSERT INTO users (tg_id, created_at, last_seen) VALUES (?,?,?) "
            "ON CONFLICT(tg_id) DO NOTHING",
            (tg_id, now, now),
        )
        updates: dict[str, object] = {}
        if group is not _UNSET:
            updates["group_name"] = group
        if teacher is not _UNSET:
            updates["teacher"] = teacher
        if notify is not _UNSET:
            updates["notify"] = int(bool(notify))
        if digest is not _UNSET:
            updates["digest"] = int(bool(digest))
        if updates:
            assignments = ", ".join(f"{k}=?" for k in updates)
            self.conn.execute(
                f"UPDATE users SET {assignments} WHERE tg_id=?",
                [*updates.values(), tg_id],
            )
        self.conn.execute("UPDATE users SET last_seen=? WHERE tg_id=?", (now, tg_id))
        self.conn.commit()
        user = self.user(tg_id)
        assert user is not None
        return user

    def set_group(self, tg_id: int, group: str) -> User:
        return self.upsert_user(tg_id, group=group)

    def toggle(self, tg_id: int, field: str) -> User:
        if field not in {"notify", "digest"}:
            raise ValueError(field)
        user = self.user(tg_id) or self.upsert_user(tg_id)
        current = getattr(user, field)
        if field == "notify":
            return self.upsert_user(tg_id, notify=not current)
        return self.upsert_user(tg_id, digest=not current)

    def subscribers(self, group: str | None = None) -> list[User]:
        if group:
            rows = self.conn.execute(
                "SELECT * FROM users WHERE notify=1 AND group_name=?", (group,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM users WHERE notify=1").fetchall()
        return [
            User(
                tg_id=r["tg_id"],
                group=r["group_name"],
                teacher=r["teacher"],
                notify=bool(r["notify"]),
                digest=bool(r["digest"]),
            )
            for r in rows
        ]

    def stats(self) -> dict:
        return {
            "lessons": self.count(),
            "groups": len(self.groups()),
            "teachers": len(self.teachers()),
            "users": int(self.conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]),
            "subscribers": int(
                self.conn.execute("SELECT COUNT(*) AS c FROM users WHERE notify=1").fetchone()["c"]
            ),
            "sources": int(self.conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]),
        }


def _lesson_from_db(row: sqlite3.Row) -> Lesson:
    return Lesson(
        date=date.fromisoformat(row["date"]),
        start=row["start"],
        end=row["end"] or "",
        group=row["group_name"],
        subject=row["subject"] or "",
        room=row["room"] or "",
        teacher=row["teacher"] or "",
        subgroup=row["subgroup"] or "",
    )
