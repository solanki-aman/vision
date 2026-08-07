"""Undo walks the stack — the regression guard for the ping-pong bug.

Undo applies a change set's stored inverse as a *new* change set, so that new
one is itself applied and carries its own inverse. If it can be chosen as the
next undo's target, a second undo just redoes what the first removed. These
tests need a real database; they skip cleanly when one isn't reachable.
"""

import os

import pytest

from app.commands import apply_change_set, undo_last
from app.db import close_db, get_canvas_state, init_db, pool, uid

pytestmark = pytest.mark.asyncio


def kpi(title: str, value: float) -> dict:
    return {
        "kind": "add_widget",
        "widgetKind": "kpi",
        "title": title,
        "spec": {"value": value, "label": "test"},
        "provenance": {"source": "test", "confidence": "illustrative"},
    }


@pytest.fixture
async def canvas():
    try:
        await init_db()
    except Exception as e:  # noqa: BLE001 — no database in this environment
        pytest.skip(f"database unavailable: {e}")
    row = await pool().fetchrow(
        "INSERT INTO canvas.canvases (title) VALUES ('__undo_test__') RETURNING id"
    )
    cid = str(row["id"])
    try:
        yield cid
    finally:
        await pool().execute("DELETE FROM canvas.canvases WHERE id = $1", uid(cid))
        await close_db()


async def titles(canvas_id: str) -> list[str]:
    state = await get_canvas_state(canvas_id)
    return sorted(w["title"] for w in state["widgets"])


async def test_repeated_undo_walks_back_through_the_stack(canvas):
    for name, value in (("one", 1.0), ("two", 2.0), ("three", 3.0)):
        await apply_change_set(canvas, [kpi(name, value)], "agent")
    assert await titles(canvas) == ["one", "three", "two"]

    await undo_last(canvas)
    assert await titles(canvas) == ["one", "two"]

    # The bug: this second undo used to target the undo itself and put "three" back.
    await undo_last(canvas)
    assert await titles(canvas) == ["one"]

    await undo_last(canvas)
    assert await titles(canvas) == []


async def test_undo_returns_none_when_there_is_nothing_left_to_undo(canvas):
    await apply_change_set(canvas, [kpi("only", 1.0)], "agent")
    assert await undo_last(canvas) is not None
    assert await undo_last(canvas) is None


async def test_undo_preserves_history_rather_than_deleting_it(canvas):
    await apply_change_set(canvas, [kpi("kept", 1.0)], "agent")
    await undo_last(canvas)
    count = await pool().fetchval(
        "SELECT count(*) FROM canvas.change_sets WHERE canvas_id = $1", uid(canvas)
    )
    # The original edit and the undo that reversed it both remain on the record.
    assert count >= 2
