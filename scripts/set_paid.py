#!/usr/bin/env python3
"""Подтверждение оплаты подписки (FR-A7).

Применяет команду «отель заплатил»: ставит hotel.paid_until на указанную дату.
Автоматического биллинга нет — скрипт запускает команда вручную (вне объёма UI,
решение A-7).

Использование (из корня проекта, с тем же DATABASE_PATH, что и у приложения):

    python scripts/set_paid.py "Отель «Ромашка»" 2026-12-01
    python scripts/set_paid.py --login manager 2026-12-01
    python scripts/set_paid.py --list

Без аргумента даты показывает текущее состояние подписок.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timezone
from pathlib import Path

# Импорт приложения из корня проекта (скрипт лежит в scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, models  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Подтвердить оплату подписки (FR-A7)")
    parser.add_argument("hotel", nargs="?",
                        help="название отеля или --login логина управляющего")
    parser.add_argument("until", nargs="?", metavar="ГГГГ-ММ-ДД",
                        help="оплачено до этой даты (включительно, конец дня UTC)")
    parser.add_argument("--login", metavar="LOGIN",
                        help="найти отель по логину пользователя")
    parser.add_argument("--list", action="store_true", help="показать все отели")
    args = parser.parse_args()

    settings = get_settings()
    db.init_engine(settings)
    db.init_db()  # на пустой базе покажет список без отелей вместо traceback

    session = db.new_session()
    try:
        if args.list or not args.hotel and not args.login:
            for hotel in session.query(models.Hotel).order_by(models.Hotel.id):
                trial = hotel.trial_ends_at.date()
                paid = hotel.paid_until.date() if hotel.paid_until else "—"
                print(f"#{hotel.id} {hotel.name}: пилот до {trial}, оплачено до {paid}")
            return 0

        if args.login:
            user = session.query(models.User).filter_by(login=args.login.lower()).first()
            if user is None:
                print(f"Пользователь '{args.login}' не найден", file=sys.stderr)
                return 2
            hotel = session.get(models.Hotel, user.hotel_id)
        else:
            hotel = session.query(models.Hotel).filter_by(name=args.hotel).first()
            if hotel is None:
                print(f"Отель '{args.hotel}' не найден", file=sys.stderr)
                return 2

        if not args.until:
            trial = hotel.trial_ends_at.date()
            paid = hotel.paid_until.date() if hotel.paid_until else "—"
            print(f"{hotel.name}: пилот до {trial}, оплачено до {paid}")
            return 0

        until_date = datetime.strptime(args.until, "%Y-%m-%d").date()
        # «оплачено до ДД.ММ.ГГГГ» — до конца этого дня (UTC; сравнение с utcnow)
        paid_until = datetime.combine(until_date, time(23, 59, 59), tzinfo=timezone.utc)
        paid_until = paid_until.replace(tzinfo=None)

        with db.immediate_transaction(session):
            hotel.paid_until = paid_until
            session.commit()
        print(f"{hotel.name}: оплачено до {until_date:%d.%m.%Y} — готово")
        return 0
    finally:
        session.close()
        db.reset_engine()


if __name__ == "__main__":
    raise SystemExit(main())
