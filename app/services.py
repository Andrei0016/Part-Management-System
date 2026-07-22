"""Shared operations used by the web blueprints and the internal API.

Centralizing these here means every write path (UI or MCP-driven) logs the
same way and marks the sync state dirty the same way.
"""
from app.extensions import db
from app.models import Box, LogEntry, Part, SyncState


class ServiceError(ValueError):
    """Raised for user-facing validation failures (bad id, insufficient stock, etc.)."""


def log_action(user, action, part_id=None, quantity_delta=None, note=None):
    entry = LogEntry(
        user_id=user.id,
        action=action,
        part_id=part_id,
        quantity_delta=quantity_delta,
        note=note,
    )
    db.session.add(entry)


def adjust_stock(part: Part, delta: int, user, note=None):
    """Take (negative delta) or add (positive delta) stock on a part."""
    if delta == 0:
        raise ServiceError("Quantity change cannot be zero.")
    new_quantity = part.quantity + delta
    if new_quantity < 0:
        raise ServiceError(
            f"Cannot take {-delta} of '{part.name}' — only {part.quantity} in stock."
        )
    part.quantity = new_quantity
    log_action(
        user,
        action="add" if delta > 0 else "take",
        part_id=part.id,
        quantity_delta=delta,
        note=note,
    )
    SyncState.mark_dirty()
    db.session.commit()
    return part


def register_part(part_id, box_id, name, quantity, minimum_quantity, tags, user):
    if Part.query.get(part_id) is not None:
        raise ServiceError(f"Part '{part_id}' already exists.")
    if Box.query.get(box_id) is None:
        raise ServiceError(f"Box '{box_id}' does not exist.")
    part = Part(
        id=part_id,
        box_id=box_id,
        name=name,
        quantity=quantity,
        minimum_quantity=minimum_quantity,
        tags=tags,
    )
    db.session.add(part)
    log_action(user, action="register", part_id=part_id, note=f"Registered part '{name}'")
    SyncState.mark_dirty()
    db.session.commit()
    return part


def edit_part(part: Part, name, quantity, minimum_quantity, tags, user):
    part.name = name
    part.quantity = quantity
    part.minimum_quantity = minimum_quantity
    part.tags = tags
    log_action(user, action="edit", part_id=part.id, note=f"Edited part '{name}'")
    SyncState.mark_dirty()
    db.session.commit()
    return part


def register_box(box_id, shelf, row, user):
    if Box.query.get(box_id) is not None:
        raise ServiceError(f"Box '{box_id}' already exists.")
    box = Box(box_id=box_id, shelf=shelf, row=row)
    db.session.add(box)
    log_action(user, action="register", note=f"Registered box '{box_id}' ({shelf}/{row})")
    SyncState.mark_dirty()
    db.session.commit()
    return box


def edit_box(box: Box, shelf, row, user):
    box.shelf = shelf
    box.row = row
    log_action(user, action="edit", note=f"Edited box '{box.box_id}' ({shelf}/{row})")
    SyncState.mark_dirty()
    db.session.commit()
    return box


def search_parts(query=None, tag=None):
    q = Part.query
    if query:
        like = f"%{query}%"
        q = q.filter(db.or_(Part.name.ilike(like), Part.id.ilike(like)))
    if tag:
        # tags is a comma-separated string; match tag as a whole token.
        q = q.filter(Part.tags.ilike(f"%{tag}%"))
    parts = q.order_by(Part.name).all()
    if tag:
        parts = [p for p in parts if tag in p.tag_list]
    return parts


def all_tags():
    tags = set()
    for (tag_str,) in db.session.query(Part.tags).all():
        tags.update(t.strip() for t in tag_str.split(",") if t.strip())
    return sorted(tags)


def low_stock_parts():
    return [p for p in Part.query.order_by(Part.name).all() if p.is_low_stock]
