"""CLI управления Layla.

Запуск в контейнере (логин в UI не требуется):
  docker compose exec layla-core python -m app.manage reset-admin
  docker compose exec layla-core python -m app.manage reset-admin --email you@example.com
  docker compose exec layla-core python -m app.manage list-users

reset-admin выдаёт указанному пользователю права администратора и печатает новый
случайный пароль. Если пользователь не найден — создаёт его. Без --email берётся
первый администратор, иначе первый пользователь, иначе создаётся admin из настроек.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models.user import User
from app.security.passwords import generate_password, hash_password


async def _reset_admin(email: str | None) -> None:
    async with SessionLocal() as session:
        user: User | None = None
        if email:
            email = email.lower().strip()
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, pw_hash="", display_name="Администратор")
                session.add(user)
        else:
            user = await session.scalar(
                select(User).where(User.is_admin.is_(True)).order_by(User.created_at)
            )
            if user is None:
                user = await session.scalar(select(User).order_by(User.created_at))
            if user is None:
                user = User(
                    email=get_settings().admin_email.lower().strip(),
                    pw_hash="",
                    display_name="Администратор",
                )
                session.add(user)

        password = generate_password()
        user.pw_hash = hash_password(password)
        user.is_admin = True
        user.is_active = True
        user.must_change_password = True
        await session.commit()
        print("=" * 48)
        print("Логин: ", user.email)
        print("Пароль:", password)
        print("=" * 48)
        print("Смените пароль после входа: Настройки → Общие.")


async def _list_users() -> None:
    async with SessionLocal() as session:
        users = (await session.scalars(select(User).order_by(User.created_at))).all()
        if not users:
            print("Пользователей нет.")
            return
        for u in users:
            flags = []
            if u.is_admin:
                flags.append("админ")
            if not u.is_active:
                flags.append("отключён")
            print(f"- {u.email}  {'(' + ', '.join(flags) + ')' if flags else ''}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    reset = sub.add_parser("reset-admin", help="сбросить пароль администратора")
    reset.add_argument("--email", default=None, help="e-mail пользователя (по умолчанию — первый админ)")
    sub.add_parser("list-users", help="список пользователей")
    args = parser.parse_args()

    if args.cmd == "reset-admin":
        asyncio.run(_reset_admin(args.email))
    elif args.cmd == "list-users":
        asyncio.run(_list_users())


if __name__ == "__main__":
    main()
