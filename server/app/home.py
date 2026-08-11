"""Home: sections, pins, schedules, findings, and per-viewer tile resolution.

A pin references a widget; it never copies one (D-3). What a viewer actually sees is
computed at read time: the widget's spec template plus the facts *their* entitlements
produce. Two people can hold the same pin and see different numbers, and neither
response ever carries the other's values.

The rule that makes sharing safe is in `resolve_tile`: for an `entitled` query the
tile is re-executed as the viewer, and the fact map handed to `materialize` is keyed by
the *binding's* fact id but filled with the *viewer's* fact. For a `public` query the
owner's snapshot is served as-is, because a web search has no viewer-specific answer
and re-running it would cost money to produce a different result for no reason.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from . import finance, queries
from .db import pool, uid

log = logging.getLogger("vision.home")

# Seeded on first use, then owned by the user — renameable, reorderable, deletable
# (D-1). Only `brief` is reserved, because the ambient assembler looks it up by key.
SEED_SECTIONS = [
    ("brief", "Morning brief"),
    ("revenue", "Revenue"),
    ("margin", "Margin & cost"),
    ("cash", "Cash"),
    ("plan", "Plan vs actual"),
    ("market", "Market"),
    ("filings", "Filings & docs"),
]

CLOCK_CADENCES = {
    "manual": "Manual only",
    "every_15m": "Every 15 minutes",
    "hourly": "Hourly",
    "daily_0700": "Daily at 07:00",
    "weekly_mon": "Weekly, Monday 07:00",
}
FISCAL_CADENCES = {
    "month_close": "Month close",
    "quarter_close": "Quarter close",
    "year_end": "Fiscal year end",
}
EVENT_CADENCES = {
    "source_update": "When the source updates",
    "market_open": "Market open",
    "market_close": "Market close",
    "before_earnings": "Before earnings (T-2)",
    "after_earnings": "After earnings",
    "on_filing": "On a new filing",
}

# Minutes between runs, for the cadences that are really just intervals.
INTERVALS = {"every_15m": 15, "hourly": 60, "daily_0700": 1440, "weekly_mon": 10080}


SCHEMA = """
CREATE SCHEMA IF NOT EXISTS home;

CREATE TABLE IF NOT EXISTS home.sections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_subject TEXT NOT NULL,
  key   TEXT NOT NULL,
  title TEXT NOT NULL,
  ord   INT  NOT NULL,
  UNIQUE (owner_subject, key)
);

CREATE TABLE IF NOT EXISTS home.prefs (
  owner_subject TEXT PRIMARY KEY,
  brief_hour INT NOT NULL DEFAULT 7,
  timezone   TEXT NOT NULL DEFAULT 'UTC',
  brief_built_on DATE
);

CREATE TABLE IF NOT EXISTS home.pins (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_subject TEXT NOT NULL,
  section_id UUID NOT NULL REFERENCES home.sections(id) ON DELETE CASCADE,
  widget_id  UUID NOT NULL REFERENCES canvas.widgets(id) ON DELETE CASCADE,
  canvas_id  UUID NOT NULL REFERENCES canvas.canvases(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  ord INT NOT NULL DEFAULT 0,
  w INT NOT NULL DEFAULT 4,
  h INT NOT NULL DEFAULT 3,
  -- the last materialisation: a cache, never the source of truth for a bound leaf
  cached_spec JSONB,
  cached_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'ok',      -- ok | stale | unavailable
  status_reason TEXT,
  watch JSONB,                            -- { path, op, value } — when to speak
  changed_at TIMESTAMPTZ,                 -- last time a refresh moved a number
  seen_at TIMESTAMPTZ,
  claimed_at TIMESTAMPTZ,
  attempts INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pins_owner ON home.pins (owner_subject, section_id, ord);

CREATE TABLE IF NOT EXISTS home.schedules (
  pin_id UUID PRIMARY KEY REFERENCES home.pins(id) ON DELETE CASCADE,
  family TEXT NOT NULL,                   -- clock | fiscal | event
  kind   TEXT NOT NULL,
  params JSONB,
  enabled BOOLEAN NOT NULL DEFAULT true,
  next_run_at TIMESTAMPTZ,
  last_ok_at TIMESTAMPTZ,
  last_error TEXT,
  attempts INT NOT NULL DEFAULT 0,
  watermark TEXT                          -- for source_update: the value last seen
);
CREATE INDEX IF NOT EXISTS idx_schedules_due
  ON home.schedules (next_run_at) WHERE enabled;

CREATE TABLE IF NOT EXISTS home.section_grants (
  section_id UUID NOT NULL REFERENCES home.sections(id) ON DELETE CASCADE,
  principal  TEXT NOT NULL,
  granted_by TEXT NOT NULL,
  granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (section_id, principal)
);
CREATE INDEX IF NOT EXISTS idx_section_grants_principal
  ON home.section_grants (principal);

CREATE TABLE IF NOT EXISTS home.findings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_subject TEXT NOT NULL,
  -- SET NULL, not CASCADE (D-9): a finding about a tile the user deleted *because of
  -- the finding* is the one most worth keeping. It leaves the brief and the Inbox and
  -- stays readable in Activity.
  pin_id UUID REFERENCES home.pins(id) ON DELETE SET NULL,
  pin_title TEXT,
  run_id UUID,
  kind TEXT NOT NULL,             -- moved | crossed | broke | stale | absent
  interaction TEXT NOT NULL,      -- notify | question | review
  allowed JSONB,                  -- accept | edit | respond | ignore
  headline TEXT NOT NULL,
  detail TEXT,
  bindings JSONB,
  access_class TEXT NOT NULL DEFAULT 'entitled',
  narrowed JSONB,
  score NUMERIC NOT NULL DEFAULT 0,
  surfaced_at TIMESTAMPTZ,
  dismissed_at TIMESTAMPTZ,
  acted_at TIMESTAMPTZ,
  suppressed_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_findings_owner
  ON home.findings (owner_subject, created_at DESC);
"""


# ---- sections and prefs ------------------------------------------------------------------


async def ensure_sections(subject: str) -> list[dict[str, Any]]:
    """Seed this person's sections on first use, then leave them alone."""
    rows = await pool().fetch(
        "SELECT id, key, title, ord FROM home.sections WHERE owner_subject = $1 ORDER BY ord",
        subject,
    )
    if rows:
        return [dict(r) for r in rows]
    async with pool().acquire() as conn:
        async with conn.transaction():
            for i, (key, title) in enumerate(SEED_SECTIONS):
                await conn.execute(
                    """INSERT INTO home.sections (owner_subject, key, title, ord)
                       VALUES ($1,$2,$3,$4) ON CONFLICT (owner_subject, key) DO NOTHING""",
                    subject, key, title, i,
                )
            await conn.execute(
                "INSERT INTO home.prefs (owner_subject) VALUES ($1) ON CONFLICT DO NOTHING",
                subject,
            )
    rows = await pool().fetch(
        "SELECT id, key, title, ord FROM home.sections WHERE owner_subject = $1 ORDER BY ord",
        subject,
    )
    return [dict(r) for r in rows]


async def rename_section(section_id: Any, subject: str, title: str) -> bool:
    row = await pool().fetchrow(
        """UPDATE home.sections SET title = $3
           WHERE id = $1 AND owner_subject = $2 RETURNING id""",
        uid(section_id), subject, title[:60],
    )
    return row is not None


async def prefs(subject: str) -> dict[str, Any]:
    row = await pool().fetchrow(
        "SELECT brief_hour, timezone, brief_built_on FROM home.prefs WHERE owner_subject = $1",
        subject,
    )
    if row is None:
        await pool().execute(
            "INSERT INTO home.prefs (owner_subject) VALUES ($1) ON CONFLICT DO NOTHING", subject
        )
        return {"brief_hour": 7, "timezone": "UTC", "brief_built_on": None}
    return dict(row)


# ---- the query graph behind a widget ------------------------------------------------------


async def widget_queries(widget_id: Any) -> list[dict[str, Any]]:
    """Every query a widget's bindings reach, with the binding fact that reaches it.

    This is the walk that makes the pin modal honest: it is how we know whether a tile
    can refresh at all, and what its share mode has to be.
    """
    rows = await pool().fetch(
        """SELECT f.id AS fact_id, f.label, f.kind AS fact_kind,
                  q.id AS query_id, q.source, q.access_class, q.op, q.params
           FROM canvas.widgets w
           CROSS JOIN LATERAL jsonb_array_elements(COALESCE(w.bindings, '[]'::jsonb)) b
           JOIN canvas.facts f ON f.id = (b->>'factId')::uuid
           LEFT JOIN canvas.queries q ON q.id = f.query_id
           WHERE w.id = $1""",
        uid(widget_id),
    )
    return [dict(r) for r in rows]


def share_mode(links: Sequence[dict[str, Any]]) -> str:
    """Derived, never chosen. Any entitled input makes the whole tile live-only."""
    classes = {(l.get("access_class") or "entitled") for l in links}
    if not classes:
        return "snapshot"
    return "snapshot" if classes == {"public"} else "live"


def refreshability(links: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Which bound numbers can actually be produced again, and which are frozen.

    Offering a daily refresh on a number that physically cannot move is the kind of
    lie that makes people stop trusting a dashboard, so the modal reports this rather
    than quietly accepting the schedule.
    """
    live, frozen = [], []
    for l in links:
        source = l.get("source")
        if source and queries.REFRESHABLE.get(source):
            live.append(l.get("label") or "a value")
        else:
            frozen.append(l.get("label") or "a value")
    return {
        "refreshable": len(live),
        "frozen": len(frozen),
        "frozenLabels": frozen[:4],
        "anyRefreshable": bool(live),
    }


async def pin_preview(widget_id: Any, subject: str) -> dict[str, Any]:
    links = await widget_queries(widget_id)
    sections = await ensure_sections(subject)
    sources = [l.get("source") for l in links if l.get("source")]
    suggested = "market" if sources and set(sources) == {"web"} else "revenue"
    warehouse = any(s == "warehouse" for s in sources)
    return {
        "sections": [{"id": str(s["id"]), "key": s["key"], "title": s["title"]} for s in sections],
        "suggestedSection": suggested,
        "shareMode": share_mode(links),
        "refreshability": refreshability(links),
        # D-4: warehouse-backed tiles default to the only cadence that cannot be wrong.
        "suggestedCadence": "source_update" if warehouse else "manual",
        "cadences": {
            "clock": CLOCK_CADENCES,
            "fiscal": FISCAL_CADENCES,
            "event": EVENT_CADENCES,
        },
        "bindings": [
            {"label": l.get("label"), "source": l.get("source") or "frozen",
             "accessClass": l.get("access_class") or "unknown"}
            for l in links
        ],
    }


# ---- pins -----------------------------------------------------------------------------------


async def create_pin(
    subject: str,
    *,
    widget_id: Any,
    section_key: str,
    schedule: dict[str, Any] | None = None,
    watch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sections = await ensure_sections(subject)
    section = next((s for s in sections if s["key"] == section_key), None)
    if section is None:
        section = sections[0]

    widget = await pool().fetchrow(
        "SELECT id, canvas_id, title, kind, spec FROM canvas.widgets WHERE id = $1",
        uid(widget_id),
    )
    if widget is None:
        raise ValueError("widget not found")

    ord_ = await pool().fetchval(
        "SELECT COALESCE(max(ord), -1) + 1 FROM home.pins WHERE section_id = $1", section["id"]
    )
    row = await pool().fetchrow(
        """INSERT INTO home.pins
             (owner_subject, section_id, widget_id, canvas_id, title, ord, w, h,
              cached_spec, cached_at, watch)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,now(),$10)
           RETURNING id, section_id, widget_id, canvas_id, title, ord, w, h, status""",
        subject, section["id"], widget["id"], widget["canvas_id"], widget["title"],
        ord_, 4, 3, widget["spec"], watch,
    )
    pin = dict(row)
    if schedule and schedule.get("kind") and schedule.get("kind") != "manual":
        await set_schedule(pin["id"], schedule)
    return pin


async def delete_pin(pin_id: Any, subject: str) -> bool:
    row = await pool().fetchrow(
        "DELETE FROM home.pins WHERE id = $1 AND owner_subject = $2 RETURNING id",
        uid(pin_id), subject,
    )
    return row is not None


async def move_pin(pin_id: Any, subject: str, section_key: str, ord_: int) -> bool:
    sections = await ensure_sections(subject)
    section = next((s for s in sections if s["key"] == section_key), None)
    if section is None:
        return False
    row = await pool().fetchrow(
        """UPDATE home.pins SET section_id = $3, ord = $4
           WHERE id = $1 AND owner_subject = $2 RETURNING id""",
        uid(pin_id), subject, section["id"], ord_,
    )
    return row is not None


async def mark_seen(subject: str) -> None:
    await pool().execute(
        "UPDATE home.pins SET seen_at = now() WHERE owner_subject = $1", subject
    )


# ---- financial pulse: the CFO's glance ---------------------------------------------------------


def _agg(series: list[float], mode: str = "sum") -> tuple[float, float]:
    """A trailing-quarter figure and its change against the prior quarter.

    Returns (current, prior). CFOs read in quarters, so the headline is a 3-month
    aggregate rather than a single month, with the sparkline carrying the monthly shape.
    """
    last3, prior3 = series[-3:], series[-6:-3]
    if not last3:
        return 0.0, 0.0
    if mode == "avg":
        cur = sum(last3) / len(last3)
        prev = sum(prior3) / len(prior3) if prior3 else cur
    else:
        cur = sum(last3)
        prev = sum(prior3) if prior3 else cur
    return cur, prev


async def pulse(principals: Sequence[str]) -> dict[str, Any]:
    """Headline finance metrics for the Home glance, computed as the caller.

    Real warehouse data, entitlement-filtered — so a US-only analyst gets a US-only
    pulse and is told so. Each metric carries a trailing-quarter value, its change
    against the prior quarter, and a 12-month sparkline.
    """
    result = await finance.execute("revenue_trend", {"months": 12}, principals)
    rows = result.rows
    if not rows:
        return {"metrics": [], "asOf": result.as_of, "narrowed": bool(result.withheld),
                "withheld": result.withheld or None}

    rev = [float(r["revenue"]) for r in rows]
    cogs = [float(r["cogs"]) for r in rows]
    opex = [float(r["opex"]) for r in rows]
    book = [float(r["bookings"]) for r in rows]
    margin = [(rev[i] - cogs[i]) / rev[i] * 100 if rev[i] else 0.0 for i in range(len(rev))]
    oi = [rev[i] - cogs[i] - opex[i] for i in range(len(rev))]

    def pct(cur: float, prev: float) -> float:
        return (cur - prev) / abs(prev) * 100 if prev else 0.0

    rev_c, rev_p = _agg(rev)
    oi_c, oi_p = _agg(oi)
    book_c, book_p = _agg(book)
    m_c, m_p = _agg(margin, "avg")

    metrics = [
        {"key": "revenue", "label": "Revenue", "format": "currency", "value": rev_c,
         "delta": pct(rev_c, rev_p), "deltaKind": "pct", "spark": rev, "favorable": "up"},
        {"key": "margin", "label": "Gross margin", "format": "percent", "value": m_c,
         "delta": (m_c - m_p) * 100, "deltaKind": "bp", "spark": margin, "favorable": "up"},
        {"key": "oi", "label": "Operating income", "format": "currency", "value": oi_c,
         "delta": pct(oi_c, oi_p), "deltaKind": "pct", "spark": oi, "favorable": "up"},
        {"key": "bookings", "label": "Bookings", "format": "currency", "value": book_c,
         "delta": pct(book_c, book_p), "deltaKind": "pct", "spark": book, "favorable": "up"},
    ]
    return {"metrics": metrics, "asOf": result.as_of, "narrowed": bool(result.withheld),
            "withheld": result.withheld or None}


# ---- schedules --------------------------------------------------------------------------------


def _family_for(kind: str) -> str:
    if kind in CLOCK_CADENCES:
        return "clock"
    if kind in FISCAL_CADENCES:
        return "fiscal"
    return "event"


async def set_schedule(pin_id: Any, schedule: dict[str, Any]) -> None:
    kind = schedule.get("kind") or "manual"
    if kind == "manual":
        await pool().execute("DELETE FROM home.schedules WHERE pin_id = $1", uid(pin_id))
        return
    interval = INTERVALS.get(kind)
    await pool().execute(
        """INSERT INTO home.schedules (pin_id, family, kind, params, enabled, next_run_at, watermark)
           VALUES ($1,$2,$3,$4,true,
                   CASE WHEN $5::int IS NULL THEN now() ELSE now() + make_interval(mins => $5::int) END,
                   $6)
           ON CONFLICT (pin_id) DO UPDATE
             SET family = EXCLUDED.family, kind = EXCLUDED.kind, params = EXCLUDED.params,
                 enabled = true, next_run_at = EXCLUDED.next_run_at, attempts = 0,
                 last_error = NULL""",
        uid(pin_id), _family_for(kind), kind, schedule.get("params"), interval,
        await finance.watermark() if kind == "source_update" else None,
    )


async def due_pins(limit: int = 64, stale_after: int = 600) -> list[dict[str, Any]]:
    """Claim pins whose schedule is due. `SKIP LOCKED` keeps replicas off each other.

    Same shape as `docstore.claim_stale_documents`: a claim timestamp, an attempt
    counter, and reclaim of anything a dead worker left behind — which is what makes
    the row itself the durable job.
    """
    rows = await pool().fetch(
        """UPDATE home.pins p SET claimed_at = now(), attempts = p.attempts + 1
           WHERE p.id IN (
             SELECT s.pin_id FROM home.schedules s
             JOIN home.pins pp ON pp.id = s.pin_id
             WHERE s.enabled
               AND (s.next_run_at IS NULL OR s.next_run_at <= now())
               AND (pp.claimed_at IS NULL OR pp.claimed_at < now() - make_interval(secs => $2))
             ORDER BY s.next_run_at NULLS FIRST
             LIMIT $1
             FOR UPDATE SKIP LOCKED
           )
           RETURNING p.id, p.owner_subject, p.section_id, p.widget_id, p.canvas_id,
                     p.title, p.cached_spec, p.watch, p.attempts""",
        limit, stale_after,
    )
    return [dict(r) for r in rows]


async def reschedule(pin_id: Any, *, ok: bool, error: str | None = None) -> None:
    kind = await pool().fetchval("SELECT kind FROM home.schedules WHERE pin_id = $1", uid(pin_id))
    interval = INTERVALS.get(kind or "", 60)
    await pool().execute(
        """UPDATE home.schedules
             SET next_run_at = now() + make_interval(mins => $2),
                 last_ok_at = CASE WHEN $3 THEN now() ELSE last_ok_at END,
                 last_error = $4,
                 attempts = CASE WHEN $3 THEN 0 ELSE attempts + 1 END,
                 enabled = CASE WHEN NOT $3 AND attempts >= 5 THEN false ELSE enabled END
           WHERE pin_id = $1""",
        uid(pin_id), interval, ok, error,
    )
    await pool().execute("UPDATE home.pins SET claimed_at = NULL WHERE id = $1", uid(pin_id))


# ---- per-viewer resolution ---------------------------------------------------------------------


async def resolve_tile(
    pin: dict[str, Any], principals: Sequence[str], *, execute: bool = True
) -> dict[str, Any]:
    """What this viewer should see for this pin.

    Public-only tiles serve the owner's snapshot. Anything entitled is re-executed as
    the viewer, and the response is assembled from *their* facts — the owner's values
    never enter it.
    """
    widget = await pool().fetchrow(
        """SELECT id, kind, title, spec, bindings, provenance
           FROM canvas.widgets WHERE id = $1 AND status = 'active'""",
        uid(pin["widget_id"]),
    )
    if widget is None:
        return {"status": "unavailable", "reason": "the source widget was deleted"}

    links = await widget_queries(pin["widget_id"])
    mode = share_mode(links)
    spec = widget["spec"]
    withheld: dict[str, int] = {}
    status = "ok"

    if mode == "live" and execute:
        # binding fact id → its query, so a viewer's fresh fact can stand in its place
        fact_to_query = {str(l["fact_id"]): l["query_id"] for l in links if l.get("query_id")}
        substitutes: dict[str, dict[str, Any]] = {}
        for fact_id, query_id in fact_to_query.items():
            query = await queries.get_query(query_id)
            if query is None or query["access_class"] == "public":
                continue
            result = await queries.run_query(query, principals)
            withheld.update(result.withheld)
            if result.status == "denied":
                status = "denied"
                continue
            if result.status == "failed":
                status = "stale"
                continue
            if result.facts:
                substitutes[fact_id] = result.facts[0]
        if substitutes:
            spec = queries.materialize(widget["kind"], widget["spec"], widget["bindings"], substitutes)
            if withheld:
                status = "narrowed"

    return {
        "status": status,
        "kind": widget["kind"],
        "title": pin.get("title") or widget["title"],
        "spec": spec,
        "provenance": widget["provenance"],
        "shareMode": mode,
        "withheld": withheld or None,
    }


async def list_pins(subject: str) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """SELECT p.id, p.section_id, p.widget_id, p.canvas_id, p.title, p.ord, p.w, p.h,
                  p.cached_spec, p.status, p.status_reason, p.changed_at, p.seen_at,
                  s.kind AS cadence, s.enabled AS schedule_enabled, s.last_ok_at,
                  w.kind AS widget_kind, w.provenance
           FROM home.pins p
           LEFT JOIN home.schedules s ON s.pin_id = p.id
           LEFT JOIN canvas.widgets w ON w.id = p.widget_id
           WHERE p.owner_subject = $1
           ORDER BY p.ord""",
        subject,
    )
    return [dict(r) for r in rows]


async def shared_sections(principals: Sequence[str]) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """SELECT s.id, s.key, s.title, s.owner_subject
           FROM home.section_grants g
           JOIN home.sections s ON s.id = g.section_id
           WHERE g.principal = ANY($1::text[])""",
        list(principals),
    )
    return [dict(r) for r in rows]


async def grant_section(section_id: Any, subject: str, principal: str, by: str) -> bool:
    owns = await pool().fetchval(
        "SELECT 1 FROM home.sections WHERE id = $1 AND owner_subject = $2", uid(section_id), subject
    )
    if not owns:
        return False
    await pool().execute(
        """INSERT INTO home.section_grants (section_id, principal, granted_by)
           VALUES ($1,$2,$3) ON CONFLICT (section_id, principal) DO NOTHING""",
        uid(section_id), principal, by,
    )
    return True


# ---- findings ------------------------------------------------------------------------------------


async def list_findings(subject: str, *, interaction: str | None = None,
                        include_dismissed: bool = False) -> list[dict[str, Any]]:
    rows = await pool().fetch(
        """SELECT id, pin_id, pin_title, kind, interaction, allowed, headline, detail,
                  access_class, narrowed, score, surfaced_at, dismissed_at, acted_at, created_at
           FROM home.findings
           WHERE owner_subject = $1
             AND ($2::text IS NULL OR interaction = $2)
             AND ($3 OR dismissed_at IS NULL)
             AND surfaced_at IS NOT NULL
           ORDER BY score DESC, created_at DESC LIMIT 60""",
        subject, interaction, include_dismissed,
    )
    return [dict(r) for r in rows]


async def brief_for(subject: str, budget: int) -> list[dict[str, Any]]:
    """The notify findings that cleared the budget today. Empty is a correct answer."""
    rows = await pool().fetch(
        """SELECT id, pin_id, pin_title, kind, headline, detail, narrowed, score, created_at
           FROM home.findings
           WHERE owner_subject = $1 AND interaction = 'notify'
             AND dismissed_at IS NULL AND surfaced_at IS NOT NULL
             AND created_at > now() - interval '36 hours'
           ORDER BY score DESC LIMIT $2""",
        subject, budget,
    )
    return [dict(r) for r in rows]


async def dismiss_finding(finding_id: Any, subject: str) -> bool:
    """Dismissal is training signal — it raises the bar for this pin-and-kind pair."""
    row = await pool().fetchrow(
        """UPDATE home.findings SET dismissed_at = now(),
             suppressed_until = now() + interval '7 days'
           WHERE id = $1 AND owner_subject = $2 RETURNING pin_id, kind""",
        uid(finding_id), subject,
    )
    if row is None:
        return False
    if row["pin_id"]:
        await pool().execute(
            """UPDATE home.findings SET suppressed_until = now() + interval '7 days'
               WHERE owner_subject = $1 AND pin_id = $2 AND kind = $3 AND dismissed_at IS NULL""",
            subject, row["pin_id"], row["kind"],
        )
    return True


async def act_on_finding(finding_id: Any, subject: str) -> dict[str, Any] | None:
    row = await pool().fetchrow(
        """UPDATE home.findings SET acted_at = now()
           WHERE id = $1 AND owner_subject = $2
           RETURNING id, pin_id, headline, detail""",
        uid(finding_id), subject,
    )
    return dict(row) if row else None
