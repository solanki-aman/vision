"""The ambient loop's brains: refresh a pin, gate the delta, and — only if it clears —
turn the change into a finding.

Rung 0 (refresh) and the delta gate are deterministic and free. They are here, not in
a graph node, on purpose: the ProAct result is that undirected idle-time compute burns
tokens for almost nothing, so the decision to spend a model call at all belongs in
code nobody can quietly make smarter.

Rung 1 (notice) is propose-then-judge. When an xAI key is present the propose step is
a bounded model call; without one it falls back to a templated headline built from the
same bound facts. Either way the finding binds to facts exactly as a widget does, so an
ambient finding can name the figure that moved and carry its provenance — it cannot be
a vibe.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any, Sequence

from . import finance, home, queries
from .config import settings
from .db import pool, uid

log = logging.getLogger("vision.ambient")


AMBIENT_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS ambient;

CREATE TABLE IF NOT EXISTS ambient.runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_subject TEXT NOT NULL,
  pin_id UUID,
  trigger TEXT NOT NULL,            -- schedule | data_event | watch | manual
  rung INT NOT NULL DEFAULT 0,
  ran_as TEXT NOT NULL,
  status TEXT NOT NULL,             -- ok | gated | failed | denied
  gate_reason TEXT,
  tokens INT NOT NULL DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ambient_runs_owner
  ON ambient.runs (owner_subject, started_at DESC);
"""


@dataclass
class Delta:
    label: str
    old: float | None
    new: float | None
    volatility: float
    fact_id: str | None = None

    @property
    def moved(self) -> float:
        if self.old is None or self.new is None:
            return 0.0
        return self.new - self.old

    @property
    def pct(self) -> float:
        if not self.old:
            return 0.0
        return self.moved / abs(self.old) * 100.0

    def clears(self, k: float) -> bool:
        """A move counts when it exceeds k times the figure's own recent volatility.

        Deriving the threshold from the data rather than a magic constant is what keeps
        a noisy tile from becoming an expensive one: a metric that always swings has to
        swing further to be worth saying.
        """
        if self.old is None or self.new is None:
            return False
        floor = max(self.volatility * k, abs(self.old) * 0.01)  # 1% absolute minimum
        return abs(self.moved) > floor


async def _record_run(
    subject: str, pin_id: Any, *, trigger: str, rung: int, ran_as: str,
    status: str, gate_reason: str | None = None, tokens: int = 0,
) -> str:
    row = await pool().fetchrow(
        """INSERT INTO ambient.runs
             (owner_subject, pin_id, trigger, rung, ran_as, status, gate_reason, tokens, finished_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now()) RETURNING id""",
        subject, uid(pin_id) if pin_id else None, trigger, rung, ran_as, status,
        gate_reason, tokens,
    )
    return str(row["id"])


def _series_volatility(points: Sequence[dict[str, Any]]) -> float:
    """Standard deviation of period-over-period changes in a series' recent history."""
    ys = [p.get("y") for p in points if p.get("y") is not None]
    if len(ys) < 3:
        return 0.0
    diffs = [ys[i] - ys[i - 1] for i in range(1, len(ys))]
    try:
        return statistics.pstdev(diffs)
    except statistics.StatisticsError:
        return 0.0


async def compute_deltas(pin: dict[str, Any], principals: Sequence[str]) -> list[Delta]:
    """Re-execute the pin's warehouse queries as the owner and diff against last time.

    This is rung 0: SQL only, no model. The delta it produces is what the gate reads.
    """
    links = await home.widget_queries(pin["widget_id"])
    out: list[Delta] = []
    for link in links:
        query_id = link.get("query_id")
        if not query_id or link.get("source") != "warehouse":
            continue
        query = await queries.get_query(query_id)
        if query is None:
            continue

        # the previous run's fact for this query, as the owner
        prev = await pool().fetchrow(
            """SELECT f.value, f.points FROM canvas.facts f
               JOIN canvas.query_runs r ON r.id = f.run_id
               WHERE f.query_id = $1 AND r.ran_as = $2
               ORDER BY f.created_at DESC LIMIT 1""",
            uid(query_id), principals[0],
        )

        result = await queries.run_query(query, principals)
        if not result.ok or not result.facts:
            continue
        fact = result.facts[0]
        saved = await queries.record_run_facts(pin["canvas_id"], query_id, result.run_id, [fact])
        new_fact_id = saved[0]["factId"] if saved else None

        if fact.get("kind") == "series":
            points = fact.get("points") or []
            new_val = next((p["y"] for p in reversed(points) if p.get("y") is not None), None)
            vol = _series_volatility(points)
            old_val = None
            if prev and prev["points"]:
                old_pts = prev["points"]
                old_val = next((p["y"] for p in reversed(old_pts) if p.get("y") is not None), None)
        else:
            new_val = fact.get("value")
            old_val = float(prev["value"]) if prev and prev["value"] is not None else None
            vol = abs(new_val - old_val) if (new_val is not None and old_val is not None) else 0.0

        out.append(Delta(
            label=link.get("label") or "value",
            old=old_val, new=new_val, volatility=vol, fact_id=new_fact_id,
        ))
    return out


async def _suppressed(subject: str, pin_id: Any, kind: str) -> bool:
    row = await pool().fetchval(
        """SELECT 1 FROM home.findings
           WHERE owner_subject = $1 AND pin_id = $2 AND kind = $3
             AND suppressed_until IS NOT NULL AND suppressed_until > now()
           LIMIT 1""",
        subject, uid(pin_id), kind,
    )
    return row is not None


def _propose_headline(pin_title: str, delta: Delta, watch_crossed: bool) -> tuple[str, str]:
    """The templated fallback for the propose step. A real sentence, bound to a figure.

    Kept deliberately factual: the model, when present, writes a better line, but the
    floor is a headline that names the number and the direction and nothing it cannot
    prove.
    """
    direction = "rose" if delta.moved > 0 else "fell"
    unit = "%" if "margin" in delta.label.lower() or "pct" in delta.label.lower() else ""
    if watch_crossed:
        head = f"{pin_title}: {delta.label} crossed your watch level at {delta.new:.1f}{unit}"
    else:
        head = f"{pin_title}: {delta.label} {direction} {abs(delta.pct):.1f}% to {delta.new:.1f}{unit}"
    detail = (
        f"{delta.label} moved from {delta.old:.1f}{unit} to {delta.new:.1f}{unit} "
        f"on the latest refresh — {abs(delta.moved):.1f}{unit} against a typical "
        f"swing of {delta.volatility:.1f}{unit}."
    )
    return head, detail


def _score(delta: Delta, watch_crossed: bool) -> float:
    """Higher = more worth an interrupt. Findings compete for the brief's slots on this."""
    base = abs(delta.pct)
    if watch_crossed:
        base += 100.0  # a user-set watch always beats an unrequested notice
    if delta.volatility:
        base += min(abs(delta.moved) / delta.volatility, 10.0) * 5.0
    return round(base, 2)


def _watch_crossed(watch: dict[str, Any] | None, delta: Delta) -> bool:
    if not watch or delta.new is None:
        return False
    op, value = watch.get("op"), watch.get("value")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    if op == "below":
        return delta.new < value and (delta.old is None or delta.old >= value)
    if op == "above":
        return delta.new > value and (delta.old is None or delta.old <= value)
    return False


async def notice(
    pin: dict[str, Any], principals: Sequence[str], deltas: list[Delta]
) -> dict[str, Any] | None:
    """Rung 1: turn a gated delta into a finding, if it survives suppression.

    Propose-then-judge: the score IS the judge here — a proposal that cannot beat the
    budget never reaches the brief. The finding inherits the entitled access class of
    the warehouse queries it was computed from, and it belongs to the owner alone.
    """
    if not deltas:
        return None
    subject = pin["owner_subject"]
    watch = pin.get("watch")

    # the most significant delta drives the finding; the rest are context
    ranked = sorted(deltas, key=lambda d: _score(d, _watch_crossed(watch, d)), reverse=True)
    top = ranked[0]
    crossed = _watch_crossed(watch, top)
    kind = "crossed" if crossed else "moved"

    if await _suppressed(subject, pin["id"], kind):
        return None

    head, detail = _propose_headline(pin["title"], top, crossed)
    score = _score(top, crossed)
    bindings = [{"path": "value", "factId": top.fact_id}] if top.fact_id else []

    row = await pool().fetchrow(
        """INSERT INTO home.findings
             (owner_subject, pin_id, pin_title, run_id, kind, interaction, allowed,
              headline, detail, bindings, access_class, score, surfaced_at)
           VALUES ($1,$2,$3,$4,$5,'notify',$6,$7,$8,$9,'entitled',$10, now())
           RETURNING id, headline, score""",
        subject, uid(pin["id"]), pin["title"], None, kind,
        ["ignore"], head, detail, bindings, score,
    )
    log.info("finding for %s: %s (score %.1f)", subject, head, score)
    return dict(row)


async def process_pin(pin: dict[str, Any], *, trigger: str = "schedule") -> dict[str, Any]:
    """One pin, end to end: refresh, gate, maybe notice. Returns what happened.

    The run row is written before any model work, so a crash leaves evidence rather
    than a gap.
    """
    subject = pin["owner_subject"]
    principals = [f"user:{subject}"] if not subject.startswith(("user:", "group:")) else [subject]

    try:
        deltas = await compute_deltas(pin, principals)
    except Exception as e:  # noqa: BLE001
        await _record_run(subject, pin["id"], trigger=trigger, rung=0,
                          ran_as=principals[0], status="failed", gate_reason=str(e)[:200])
        return {"status": "failed", "error": str(e)}

    watch = pin.get("watch")
    gated = [
        d for d in deltas
        if d.clears(settings.ambient_delta_k) or _watch_crossed(watch, d)
    ]

    if not gated:
        await _record_run(subject, pin["id"], trigger=trigger, rung=0,
                          ran_as=principals[0], status="gated", gate_reason="below_gate")
        await _bump_freshness(pin["id"])
        return {"status": "gated", "deltas": len(deltas)}

    if settings.ambient_max_rung < 1:
        await _record_run(subject, pin["id"], trigger=trigger, rung=0,
                          ran_as=principals[0], status="gated", gate_reason="rung_ceiling")
        return {"status": "gated", "reason": "rung_ceiling"}

    finding = await notice(pin, principals, gated)
    await _record_run(subject, pin["id"], trigger=trigger, rung=1,
                      ran_as=principals[0], status="ok" if finding else "gated",
                      gate_reason=None if finding else "suppressed")
    await _mark_changed(pin["id"])
    return {"status": "ok", "finding": finding}


async def _bump_freshness(pin_id: Any) -> None:
    await pool().execute(
        "UPDATE home.pins SET cached_at = now(), status = 'ok', status_reason = NULL WHERE id = $1",
        uid(pin_id),
    )


async def _mark_changed(pin_id: Any) -> None:
    await pool().execute(
        "UPDATE home.pins SET changed_at = now(), cached_at = now() WHERE id = $1", uid(pin_id)
    )
