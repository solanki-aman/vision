"""The internal warehouse stand-in, and the one gate every read goes through.

This is mock data for a fictional company, but the *gate* is not mock. Every query
runs through `execute()`, which builds the caller's entitlement predicate from
`finance.entitlements` and appends it. A caller-supplied filter can only ever narrow
what the predicate already allows — it can never widen it, because it is applied as an
additional `AND` rather than substituted into the predicate's place.

Three properties make this a real test rather than a demo:

- Asking for a region you are not entitled to returns **empty plus a withheld count**,
  never an error. An error would confirm the region exists, which is a disclosure.
- `withheld` is computed against the full dimension, so the caller can always be told
  "4 of 5 regions withheld" without being told which four.
- `fingerprint()` folds the resolved entitlement into the query cache key, so one
  principal's rows can never be served to another. That is the specific bug the whole
  design exists to prevent.

Numbers are invented. Every fact derived from this source is recorded
`confidence='illustrative'` so a mock figure can never be mistaken for a measured one
elsewhere on a canvas.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from .db import pool

log = logging.getLogger("vision.finance")

# The dimensions that carry entitlement. A dimension not in this tuple is not
# restricted — adding one here without adding it to `_predicate` would silently
# grant everything, so the two are checked against each other in the tests.
ENTITLED_DIMENSIONS = ("region", "segment")

ALL = "*"

REGIONS = [
    ("NA-US", "United States", "NA"),
    ("NA-CA", "Canada", "NA"),
    ("EMEA-UK", "United Kingdom", "EMEA"),
    ("EMEA-DE", "Germany", "EMEA"),
    ("APAC-JP", "Japan", "APAC"),
]

SEGMENTS = [
    ("PLATFORM", "Platform"),
    ("SERVICES", "Services"),
    ("HARDWARE", "Hardware"),
]


SCHEMA = """
CREATE SCHEMA IF NOT EXISTS finance;

CREATE TABLE IF NOT EXISTS finance.regions (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT
);

CREATE TABLE IF NOT EXISTS finance.segments (
  code TEXT PRIMARY KEY, name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finance.revenue_monthly (
  month DATE NOT NULL,
  region_code TEXT NOT NULL REFERENCES finance.regions(code),
  segment_code TEXT NOT NULL REFERENCES finance.segments(code),
  bookings NUMERIC NOT NULL,
  revenue  NUMERIC NOT NULL,
  cogs     NUMERIC NOT NULL,
  opex     NUMERIC NOT NULL,
  churn_pct NUMERIC NOT NULL,
  PRIMARY KEY (month, region_code, segment_code)
);
CREATE INDEX IF NOT EXISTS idx_revenue_month ON finance.revenue_monthly (month);

CREATE TABLE IF NOT EXISTS finance.headcount_monthly (
  month DATE NOT NULL,
  region_code TEXT NOT NULL REFERENCES finance.regions(code),
  function TEXT NOT NULL,
  headcount INT NOT NULL,
  cost NUMERIC NOT NULL,
  PRIMARY KEY (month, region_code, function)
);

CREATE TABLE IF NOT EXISTS finance.earnings_calendar (
  period TEXT PRIMARY KEY, report_date DATE NOT NULL, status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finance.filings (
  id SERIAL PRIMARY KEY, kind TEXT NOT NULL, period TEXT NOT NULL,
  filed_at TIMESTAMPTZ NOT NULL
);

-- Advances when an upstream load lands. This is what the "on source update" cadence
-- watches, and it is the only trigger that fires because the data moved rather than
-- because a clock did.
CREATE TABLE IF NOT EXISTS finance.load_watermark (
  table_name TEXT PRIMARY KEY, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Row-level entitlement. Same principal shape as canvas.grants ('user:…'/'group:…')
-- so one identity resolves against both without translation.
CREATE TABLE IF NOT EXISTS finance.entitlements (
  principal TEXT NOT NULL,
  dimension TEXT NOT NULL,
  value     TEXT NOT NULL,
  PRIMARY KEY (principal, dimension, value)
);
CREATE INDEX IF NOT EXISTS idx_entitlements_principal ON finance.entitlements (principal);
"""


# ---- entitlement resolution ----------------------------------------------------------


@dataclass
class Entitlement:
    """What a caller may see, per dimension. Absent dimension means: nothing."""

    scopes: dict[str, set[str]] = field(default_factory=dict)

    def allows_all(self, dimension: str) -> bool:
        return ALL in self.scopes.get(dimension, set())

    def values(self, dimension: str) -> set[str]:
        return self.scopes.get(dimension, set())

    @property
    def empty(self) -> bool:
        return not any(self.scopes.values())

    def fingerprint(self) -> str:
        """Opaque, stable, and safe to log. Folded into every cache key."""
        parts = [
            f"{dim}={','.join(sorted(vals))}"
            for dim, vals in sorted(self.scopes.items())
            if vals
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


async def entitlement_for(principals: Sequence[str]) -> Entitlement:
    """Union of every grant the caller's principals carry.

    Union, not intersection: being in a second group can only ever widen what you see,
    which is how every identity provider's group model already behaves.
    """
    if not principals:
        return Entitlement()
    rows = await pool().fetch(
        """SELECT dimension, value FROM finance.entitlements
           WHERE principal = ANY($1::text[])""",
        list(principals),
    )
    scopes: dict[str, set[str]] = {}
    for r in rows:
        scopes.setdefault(r["dimension"], set()).add(r["value"])
    return Entitlement(scopes)


def _predicate(ent: Entitlement, alias: str, start_index: int) -> tuple[str, list[Any]]:
    """The SQL fragment and parameters that narrow a query to what `ent` allows.

    Returned as an additional AND, never interpolated into a caller's filter. A
    dimension the caller holds nothing for yields `false`, so the query returns no
    rows rather than every row — the safe direction to fail.
    """
    clauses: list[str] = []
    args: list[Any] = []
    i = start_index
    for dim, column in (("region", "region_code"), ("segment", "segment_code")):
        if ent.allows_all(dim):
            continue
        allowed = ent.values(dim)
        if not allowed:
            clauses.append("false")
            continue
        clauses.append(f"{alias}.{column} = ANY(${i}::text[])")
        args.append(sorted(allowed))
        i += 1
    return (" AND ".join(clauses) if clauses else "true"), args


async def _withheld(ent: Entitlement) -> dict[str, int]:
    """How many members of each restricted dimension the caller cannot see.

    A count, never the names. This is what makes a narrowed answer honest: the tile can
    say "4 of 5 regions withheld" without disclosing which four exist.
    """
    out: dict[str, int] = {}
    for dim, table in (("region", "finance.regions"), ("segment", "finance.segments")):
        if ent.allows_all(dim):
            continue
        total = await pool().fetchval(f"SELECT count(*) FROM {table}")
        visible = len(ent.values(dim))
        if total and visible < total:
            out[dim] = int(total) - visible
    return out


# ---- the query surface ----------------------------------------------------------------


@dataclass
class QueryResult:
    rows: list[dict[str, Any]]
    withheld: dict[str, int]
    fingerprint: str
    as_of: str | None = None

    @property
    def narrowed(self) -> bool:
        return bool(self.withheld)

    @property
    def denied(self) -> bool:
        return not self.rows and bool(self.withheld)


class UnknownOp(Exception):
    """The op name is not in the registry. Never surfaced with the name echoed back."""


def _months(rows: Iterable[dict[str, Any]]) -> str | None:
    latest = max((r["month"] for r in rows), default=None)
    return latest.isoformat() if latest else None


async def _revenue_by_region(ent: Entitlement, params: dict[str, Any]) -> list[dict[str, Any]]:
    where, args = _predicate(ent, "r", 2)
    rows = await pool().fetch(
        f"""SELECT r.region_code, g.name AS region_name,
                   sum(r.revenue) AS revenue, sum(r.cogs) AS cogs, sum(r.opex) AS opex,
                   max(r.month) AS month
            FROM finance.revenue_monthly r
            JOIN finance.regions g ON g.code = r.region_code
            WHERE r.month > (SELECT max(month) FROM finance.revenue_monthly)
                             - make_interval(months => $1) AND {where}
            GROUP BY r.region_code, g.name
            ORDER BY revenue DESC""",
        int(params.get("months", 3)),
        *args,
    )
    return [dict(r) for r in rows]


async def _revenue_trend(ent: Entitlement, params: dict[str, Any]) -> list[dict[str, Any]]:
    where, args = _predicate(ent, "r", 2)
    rows = await pool().fetch(
        f"""SELECT r.month, sum(r.revenue) AS revenue, sum(r.cogs) AS cogs,
                   sum(r.opex) AS opex, sum(r.bookings) AS bookings
            FROM finance.revenue_monthly r
            WHERE r.month > (SELECT max(month) FROM finance.revenue_monthly)
                             - make_interval(months => $1) AND {where}
            GROUP BY r.month ORDER BY r.month""",
        int(params.get("months", 24)),
        *args,
    )
    return [dict(r) for r in rows]


async def _margin_by_segment(ent: Entitlement, params: dict[str, Any]) -> list[dict[str, Any]]:
    where, args = _predicate(ent, "r", 2)
    rows = await pool().fetch(
        f"""SELECT r.segment_code, s.name AS segment_name,
                   sum(r.revenue) AS revenue, sum(r.cogs) AS cogs,
                   CASE WHEN sum(r.revenue) = 0 THEN 0
                        ELSE (sum(r.revenue) - sum(r.cogs)) / sum(r.revenue) * 100 END
                     AS gross_margin_pct,
                   max(r.month) AS month
            FROM finance.revenue_monthly r
            JOIN finance.segments s ON s.code = r.segment_code
            WHERE r.month > (SELECT max(month) FROM finance.revenue_monthly)
                             - make_interval(months => $1) AND {where}
            GROUP BY r.segment_code, s.name ORDER BY revenue DESC""",
        int(params.get("months", 3)),
        *args,
    )
    return [dict(r) for r in rows]


async def _margin_trend_by_region(ent: Entitlement, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Monthly gross margin per region — the series the EMEA finding is built from."""
    where, args = _predicate(ent, "r", 2)
    rows = await pool().fetch(
        f"""SELECT r.month, r.region_code,
                   CASE WHEN sum(r.revenue) = 0 THEN 0
                        ELSE (sum(r.revenue) - sum(r.cogs)) / sum(r.revenue) * 100 END
                     AS gross_margin_pct
            FROM finance.revenue_monthly r
            WHERE r.month > (SELECT max(month) FROM finance.revenue_monthly)
                             - make_interval(months => $1) AND {where}
            GROUP BY r.month, r.region_code ORDER BY r.month, r.region_code""",
        int(params.get("months", 12)),
        *args,
    )
    return [dict(r) for r in rows]


async def _pnl_summary(ent: Entitlement, params: dict[str, Any]) -> list[dict[str, Any]]:
    where, args = _predicate(ent, "r", 2)
    row = await pool().fetchrow(
        f"""SELECT sum(r.revenue) AS revenue, sum(r.cogs) AS cogs, sum(r.opex) AS opex,
                   sum(r.revenue) - sum(r.cogs) AS gross_profit,
                   sum(r.revenue) - sum(r.cogs) - sum(r.opex) AS operating_income,
                   max(r.month) AS month
            FROM finance.revenue_monthly r
            WHERE r.month > (SELECT max(month) FROM finance.revenue_monthly)
                             - make_interval(months => $1) AND {where}""",
        int(params.get("months", 3)),
        *args,
    )
    return [dict(row)] if row and row["revenue"] is not None else []


async def _headcount_by_function(ent: Entitlement, params: dict[str, Any]) -> list[dict[str, Any]]:
    # headcount has no segment dimension, so only the region half of the predicate applies
    region_ent = Entitlement({"region": ent.values("region"), "segment": {ALL}})
    where, args = _predicate(region_ent, "h", 1)
    rows = await pool().fetch(
        f"""SELECT h.function, sum(h.headcount) AS headcount, sum(h.cost) AS cost,
                   max(h.month) AS month
            FROM finance.headcount_monthly h
            WHERE h.month = (SELECT max(month) FROM finance.headcount_monthly) AND {where}
            GROUP BY h.function ORDER BY headcount DESC""",
        *args,
    )
    return [dict(r) for r in rows]


# op name → handler. A registered name with typed params, never SQL and never a string
# the model composed. Adding an op is a code review, which is the point.
OPS = {
    "revenue_by_region": _revenue_by_region,
    "revenue_trend": _revenue_trend,
    "margin_by_segment": _margin_by_segment,
    "margin_trend_by_region": _margin_trend_by_region,
    "pnl_summary": _pnl_summary,
    "headcount_by_function": _headcount_by_function,
}

OP_DESCRIPTIONS = {
    "revenue_by_region": "Revenue, COGS and opex grouped by region for the last N months.",
    "revenue_trend": "Monthly revenue, COGS, opex and bookings over the last N months.",
    "margin_by_segment": "Gross margin percent by product segment for the last N months.",
    "margin_trend_by_region": "Monthly gross margin percent per region for the last N months.",
    "pnl_summary": "Revenue, gross profit and operating income totals for the last N months.",
    "headcount_by_function": "Current headcount and cost by function.",
}


async def execute(op: str, params: dict[str, Any], principals: Sequence[str]) -> QueryResult:
    """Run a registered op under the caller's entitlements.

    The only way into this schema. Callers hand an op name and typed params; the
    predicate is built here from stored grants and appended to whatever the op's own
    SQL already filters on.
    """
    handler = OPS.get(op)
    if handler is None:
        raise UnknownOp(op)

    ent = await entitlement_for(principals)
    rows = await handler(ent, params or {})
    withheld = await _withheld(ent)

    for r in rows:  # dates and Decimals are not JSON, and every caller wants them to be
        for k, v in list(r.items()):
            if isinstance(v, date):
                r[k] = v.isoformat()
            elif hasattr(v, "quantize"):
                r[k] = float(v)

    as_of = await pool().fetchval(
        "SELECT loaded_at FROM finance.load_watermark WHERE table_name = 'revenue_monthly'"
    )
    return QueryResult(
        rows=rows,
        withheld=withheld,
        fingerprint=ent.fingerprint(),
        as_of=as_of.isoformat() if as_of else None,
    )


async def watermark(table_name: str = "revenue_monthly") -> str | None:
    row = await pool().fetchval(
        "SELECT loaded_at FROM finance.load_watermark WHERE table_name = $1", table_name
    )
    return row.isoformat() if row else None


async def bump_watermark(table_name: str = "revenue_monthly") -> None:
    """What an upstream load would call. Drives the 'on source update' cadence."""
    await pool().execute(
        """INSERT INTO finance.load_watermark (table_name, loaded_at) VALUES ($1, now())
           ON CONFLICT (table_name) DO UPDATE SET loaded_at = now()""",
        table_name,
    )


async def next_earnings() -> dict[str, Any] | None:
    row = await pool().fetchrow(
        """SELECT period, report_date, status FROM finance.earnings_calendar
           WHERE report_date >= current_date ORDER BY report_date LIMIT 1"""
    )
    return dict(row) if row else None


# ---- seed ------------------------------------------------------------------------------

# Invented, but shaped: ~$40M a month growing at ~1.1%, with a deliberate EMEA cost
# shock in the final quarter so the ambient demo has something true to find.
_BASE_MONTHLY = {
    "NA-US": 21_000_000.0,
    "NA-CA": 4_200_000.0,
    "EMEA-UK": 6_400_000.0,
    "EMEA-DE": 5_100_000.0,
    "APAC-JP": 3_300_000.0,
}
_SEGMENT_SHARE = {"PLATFORM": 0.62, "SERVICES": 0.26, "HARDWARE": 0.12}
_SEGMENT_COGS = {"PLATFORM": 0.18, "SERVICES": 0.55, "HARDWARE": 0.68}
_FUNCTIONS = ["Engineering", "Sales", "Customer Success", "G&A", "Marketing"]


def _month_starts(n: int, end: date) -> list[date]:
    out, y, m = [], end.year, end.month
    for _ in range(n):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


async def seed(force: bool = False) -> bool:
    """Populate the warehouse. Idempotent — returns False if data was already there."""
    existing = await pool().fetchval("SELECT count(*) FROM finance.revenue_monthly")
    if existing and not force:
        return False

    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                """INSERT INTO finance.regions (code, name, parent) VALUES ($1,$2,$3)
                   ON CONFLICT (code) DO NOTHING""",
                REGIONS,
            )
            await conn.executemany(
                """INSERT INTO finance.segments (code, name) VALUES ($1,$2)
                   ON CONFLICT (code) DO NOTHING""",
                SEGMENTS,
            )

            months = _month_starts(24, date(2026, 7, 1))
            rev_rows, hc_rows = [], []
            for i, month in enumerate(months):
                growth = 1.011**i
                season = 1.0 + 0.05 * math.sin((month.month / 12) * 2 * math.pi)
                # Q-end pull-forward, the way a real bookings curve looks
                quarter_end = 1.08 if month.month in (3, 6, 9, 12) else 1.0
                for region, base in _BASE_MONTHLY.items():
                    # The story: EMEA cost of delivery jumps in the last two months.
                    shock = 0.0
                    if region.startswith("EMEA") and i >= len(months) - 2:
                        shock = 0.075
                    for segment, share in _SEGMENT_SHARE.items():
                        revenue = base * share * growth * season
                        cogs = revenue * (_SEGMENT_COGS[segment] + shock)
                        rev_rows.append(
                            (
                                month,
                                region,
                                segment,
                                round(revenue * quarter_end * 1.06, 2),  # bookings
                                round(revenue, 2),
                                round(cogs, 2),
                                round(revenue * 0.34, 2),
                                round(1.4 + 0.3 * math.sin(i / 3.0), 3),
                            )
                        )
                if i >= len(months) - 6:  # six months of headcount is enough to be useful
                    for region in _BASE_MONTHLY:
                        scale = _BASE_MONTHLY[region] / _BASE_MONTHLY["NA-US"]
                        for fn in _FUNCTIONS:
                            weight = {
                                "Engineering": 0.38,
                                "Sales": 0.24,
                                "Customer Success": 0.18,
                                "G&A": 0.09,
                                "Marketing": 0.11,
                            }[fn]
                            heads = max(1, round(620 * scale * weight * (1 + 0.004 * i)))
                            hc_rows.append(
                                (month, region, fn, heads, round(heads * 12_400.0, 2))
                            )

            await conn.executemany(
                """INSERT INTO finance.revenue_monthly
                     (month, region_code, segment_code, bookings, revenue, cogs, opex, churn_pct)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                   ON CONFLICT (month, region_code, segment_code) DO NOTHING""",
                rev_rows,
            )
            await conn.executemany(
                """INSERT INTO finance.headcount_monthly
                     (month, region_code, function, headcount, cost)
                   VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (month, region_code, function) DO NOTHING""",
                hc_rows,
            )
            await conn.executemany(
                """INSERT INTO finance.earnings_calendar (period, report_date, status)
                   VALUES ($1,$2,$3) ON CONFLICT (period) DO NOTHING""",
                [
                    ("FY26 Q1", date(2025, 10, 28), "reported"),
                    ("FY26 Q2", date(2026, 1, 27), "reported"),
                    ("FY26 Q3", date(2026, 4, 28), "reported"),
                    ("FY26 Q4", date(2026, 8, 25), "scheduled"),
                    ("FY27 Q1", date(2026, 10, 27), "scheduled"),
                ],
            )
            await conn.execute(
                """INSERT INTO finance.load_watermark (table_name, loaded_at)
                   VALUES ('revenue_monthly', now()), ('headcount_monthly', now())
                   ON CONFLICT (table_name) DO NOTHING"""
            )

            # The two principals the design is written around. Groups, not users, so a
            # real IdP can drive this by group membership without a data migration.
            await conn.executemany(
                """INSERT INTO finance.entitlements (principal, dimension, value)
                   VALUES ($1,$2,$3) ON CONFLICT DO NOTHING""",
                [
                    ("group:finance-leadership", "region", ALL),
                    ("group:finance-leadership", "segment", ALL),
                    ("user:ada@northwind.example", "region", ALL),
                    ("user:ada@northwind.example", "segment", ALL),
                    # User B: the United States and nothing else.
                    ("group:us-analysts", "region", "NA-US"),
                    ("group:us-analysts", "segment", ALL),
                    ("user:blake@northwind.example", "region", "NA-US"),
                    ("user:blake@northwind.example", "segment", ALL),
                    # Local development runs as one principal; give it the full view so
                    # the app is usable with AUTH_MODE unset.
                    ("user:local-user", "region", ALL),
                    ("user:local-user", "segment", ALL),
                ],
            )
    log.info("seeded finance warehouse: %d revenue rows", len(rev_rows))
    return True
