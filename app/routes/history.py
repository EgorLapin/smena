"""История (DF-7; FR-3.4 — FR-3.5; экран «История»).

GET /history?tab=issues|handovers&place=&date_from=&date_to=&status=&page=
Некорректные параметры — сообщение на странице и игнор фильтра (маршрутная
таблица ТЗ); валидные значения остаются в форме.
"""
from __future__ import annotations

from datetime import date
from math import ceil

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..deps import require_manager
from ..render import render
from ..services import history as h
from ..times import fmt_dt, plural
from ..validation import validate_date

router = APIRouter()

_STATUSES = ("all", "OPEN", "CLOSED")
_TABS = ("issues", "handovers")


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw or validate_date(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@router.get("/history", response_class=HTMLResponse)
def history_page(
    request: Request,
    tab: str = "issues",
    place: str = "",
    date_from: str = "",
    date_to: str = "",
    status: str = "all",
    page: str = "1",
    session: Session = Depends(get_db),
    manager: models.User = Depends(require_manager),
):
    hotel = session.get(models.Hotel, manager.hotel_id)
    assert hotel is not None
    tz = hotel.timezone

    problems: list[str] = []
    if tab not in _TABS:
        problems.append("вкладка")
        tab = "issues"
    if status not in _STATUSES:
        problems.append("статус")
        status = "all"
    try:
        page_num = max(1, int(page))
    except ValueError:
        problems.append("страница")
        page_num = 1
    d_from = _parse_date(date_from) if date_from.strip() else None
    d_to = _parse_date(date_to) if date_to.strip() else None
    if date_from.strip() and d_from is None:
        problems.append("дата «от»")
    if date_to.strip() and d_to is None:
        problems.append("дата «до»")
    if d_from and d_to and d_from > d_to:
        problems.append("интервал дат")
        d_to = None

    rows, total, total_pages = [], 0, 1
    if tab == "issues":
        rows, total = h.search_issues(
            session, manager.hotel_id, tz,
            place=place, date_from=d_from, date_to=d_to,
            status=status, page=page_num)
        total_pages = max(1, ceil(total / h.PAGE_SIZE))

    handovers = h.history_handovers(session, manager.hotel_id) if tab == "handovers" else []

    return render(
        request, "history.html",
        {
            "page_title": "История",
            "tab": tab,
            "filters": {"place": place, "date_from": date_from,
                        "date_to": date_to, "status": status},
            "filter_message": ("Параметры некорректны: "
                               + ", ".join(problems) + " — фильтр проигнорирован"
                               if problems else ""),
            "rows": [
                {"issue": issue, "last_event": fmt_dt(last_event, tz) if last_event else "",
                 "admin_name": admin.display_name if admin else ""}
                for issue, last_event, admin in rows
            ],
            "total": total,
            "total_word": plural(total, "запись", "записи", "записей"),
            "page": page_num,
            "total_pages": total_pages,
            "handovers": [
                {**row,
                 "sent_local": fmt_dt(row["handover"].sent_at, tz),
                 "accepted_local": (fmt_dt(row["handover"].accepted_at, tz)
                                    if row["handover"].accepted_at else "")}
                for row in handovers
            ],
        },
    )
