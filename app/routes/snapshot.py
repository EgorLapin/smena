"""Снимок передачи /handovers/{id} (DF-5; экран «Снимок передачи»).

GET доступен членам отеля (ADMIN и MANAGER — карта экранов ТЗ); чужой отель —
NOT_FOUND. Снимок только для чтения и никогда не меняется (FR-2.2, NFR-5):
списки берутся из handover_item, а не из текущих issue.
Кнопка «Принять смену» — ADMIN, не автору, при статусе PENDING (T07).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..deps import current_user
from ..errors import AppError
from ..render import render
from ..times import fmt_dt

router = APIRouter()


@router.get("/handovers/{handover_id}", response_class=HTMLResponse)
def handover_snapshot(
    request: Request,
    handover_id: int,
    session: Session = Depends(get_db),
    user: models.User = Depends(current_user),
):
    handover = session.get(models.Handover, handover_id)
    if handover is None or handover.hotel_id != user.hotel_id:
        raise AppError("NOT_FOUND")

    hotel = session.get(models.Hotel, user.hotel_id)
    tz = hotel.timezone
    sender = session.get(models.User, handover.sender_id)
    receiver = session.get(models.User, handover.receiver_id) if handover.receiver_id else None

    items = list(session.scalars(
        select(models.HandoverItem)
        .where(models.HandoverItem.handover_id == handover.id)
        .order_by(models.HandoverItem.id.asc())
    ))
    open_items = [i for i in items if i.status_at_handover == models.IssueStatus.OPEN]
    closed_items = [i for i in items if i.status_at_handover == models.IssueStatus.CLOSED]

    can_accept = (
        handover.status == models.HandoverStatus.PENDING
        and user.role == models.Role.ADMIN
        and user.id != handover.sender_id
    )

    return render(
        request, "handover_snapshot.html",
        {
            "page_title": "Передача смены",
            "handover": handover,
            "sender": sender,
            "sender_time": fmt_dt(handover.sent_at, tz),
            "receiver": receiver,
            "receiver_time": fmt_dt(handover.accepted_at, tz) if handover.accepted_at else "",
            "open_items": open_items,
            "closed_items": closed_items,
            "can_accept": can_accept,
            "is_pending": handover.status == models.HandoverStatus.PENDING,
        },
    )
