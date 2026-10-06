"""Publish complaint events to the live feeds, the broadcast alert feed and the people involved.

Publishing never blocks the request that triggered it: events are handed to the event loop
and delivered in the background (each socket send has its own timeout).
"""

import asyncio

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.event_bus import event_bus
from app.models.complaint import Complaint
from app.models.user import User
from app.services.complaint_service import confirmer_view, owner_view

# Notification type -> event type on the /ws/complaints feed.
MAP_EVENTS = {
    "new_complaint": "new_complaint",
    "status_update": "complaint_status_updated",
    "reassigned": "complaint_status_updated",
    "confirmed": "complaint_status_updated",
}

# Keep references so fire-and-forget tasks aren't garbage-collected mid-flight.
_background: set[asyncio.Task] = set()


def event_data(complaint: Complaint) -> dict:
    return owner_view(complaint)


def _broadcast_data(event_type: str, complaint: Complaint) -> dict:
    """City-wide alert for every signed-in user: no titles, descriptions or reporters."""
    if event_type == "new_complaint":
        message = f"New {complaint.complaint_type} complaint reported"
    elif event_type == "reassigned":
        message = f"Complaint #{complaint.complaint_id} re-assigned to {complaint.department_name}"
    elif event_type == "confirmed":
        message = f"Complaint #{complaint.complaint_id} confirmed by another citizen ({complaint.confirmation_count} total)"
    else:
        message = f"Complaint #{complaint.complaint_id} status updated to {complaint.status}"
    return {
        "type": event_type,
        "message": message,
        "data": {"id": complaint.complaint_id, "type": complaint.complaint_type, "status": complaint.status.lower()},
    }


def _owner_data(event_type: str, complaint: Complaint, public_response: str | None) -> dict:
    """Notification for the complaint's owner, sent on /ws/notifications/{user_id}."""
    if event_type == "new_complaint":
        message = (
            f"Complaint #{complaint.complaint_id} received and routed to "
            f"{complaint.department_name or 'the control room'}"
        )
    elif event_type == "reassigned":
        message = f"Your complaint #{complaint.complaint_id} was transferred to {complaint.department_name}"
    elif event_type == "confirmed":
        others = complaint.confirmation_count
        message = (
            f"{others} other citizen{'s' if others != 1 else ''} confirmed your complaint "
            f"#{complaint.complaint_id} \"{complaint.title}\""
        )
    else:
        message = f"Your complaint #{complaint.complaint_id} \"{complaint.title}\" is now {complaint.status}"
    if public_response:
        message = f"{message}: “{public_response}”"
    return {"type": event_type, "message": message, "response": public_response, "data": event_data(complaint)}


def _confirmer_data(event_type: str, complaint: Complaint, confirmation, public_response: str | None) -> dict:
    """Notification for a citizen whose duplicate report was merged into this complaint.

    Carries the confirmer's own view of the complaint, never the original reporter's text or photo.
    """
    if event_type == "reassigned":
        message = f"A complaint you confirmed (#{complaint.complaint_id}) was transferred to {complaint.department_name}"
    else:
        message = f"A complaint you confirmed (#{complaint.complaint_id}) is now {complaint.status}"
    if public_response:
        message = f"{message}: “{public_response}”"
    return {
        "type": event_type,
        "message": message,
        "response": public_response,
        "data": confirmer_view(complaint, confirmation),
    }


def _payloads(event_type: str, complaint: Complaint, public_response: str | None) -> tuple[dict, dict, list[tuple[int, dict]]]:
    # Built eagerly, while the DB session that loaded `complaint` is still usable.
    recipients = [(complaint.user_id, _owner_data(event_type, complaint, public_response))]
    if event_type in ("status_update", "reassigned"):
        recipients += [
            (c.user_id, _confirmer_data(event_type, complaint, c, public_response))
            for c in complaint.confirmations
            if c.user_id != complaint.user_id
        ]
    return (
        {"type": MAP_EVENTS[event_type], "data": event_data(complaint)},
        _broadcast_data(event_type, complaint),
        recipients,
    )


async def _send(payloads: tuple[dict, dict, list[tuple[int, dict]]]) -> None:
    map_event, broadcast, recipients = payloads
    await event_bus.publish({"target": "complaints", "message": map_event})
    await event_bus.publish({"target": "notifications", "message": broadcast})
    for user_id, message in recipients:
        await event_bus.publish({"target": "user", "user_id": user_id, "message": message})


async def _send_to_users(user_ids: list[int], message: dict) -> None:
    for user_id in user_ids:
        await event_bus.publish({"target": "user", "user_id": user_id, "message": message})


def _spawn(coroutine) -> None:
    """Run on the current event loop without waiting for it (must be called on the loop)."""
    task = asyncio.get_running_loop().create_task(coroutine)
    _background.add(task)
    task.add_done_callback(_background.discard)


def _spawn_from_sync(fn, *args) -> None:
    """From a sync (threadpool) route: schedule fn(*args) on the event loop and return immediately."""
    anyio.from_thread.run_sync(lambda: _spawn(fn(*args)))


async def publish(event_type: str, complaint: Complaint, public_response: str | None = None) -> None:
    _spawn(_send(_payloads(event_type, complaint, public_response)))


def publish_from_sync(event_type: str, complaint: Complaint, public_response: str | None = None) -> None:
    """Same as publish(), callable from FastAPI's sync (threadpool) route handlers."""
    _spawn_from_sync(_send, _payloads(event_type, complaint, public_response))


def notify_admins_from_sync(db: Session, message: dict) -> None:
    """Send a personal notification to every active admin (e.g. a new staff access request)."""
    admin_ids = list(db.scalars(select(User.user_id).where(User.role == "admin", User.account_status == "active")))
    if admin_ids:
        _spawn_from_sync(_send_to_users, admin_ids, message)


def disconnect_user_from_sync(user_id: int) -> None:
    """Close every live connection a user holds, on every worker (after their access is revoked)."""
    _spawn_from_sync(event_bus.publish, {"target": "disconnect_user", "user_id": user_id})
