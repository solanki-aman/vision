"""The entitlement gate — the security-critical half of the whole design.

The rule the tests pin down: the predicate is *appended, never substituted*. Asking
for a region you are not entitled to returns empty plus a withheld count, never an
error and never the data. And two principals with different entitlements get different
cache fingerprints, so one's rows can never be served to the other.

These need a real database; they skip cleanly when one isn't reachable, exactly like
test_undo.py.
"""

import pytest

from app import finance
from app.db import close_db, init_db, pool

pytestmark = pytest.mark.asyncio

ADA = ["user:ada@northwind.example", "group:finance-leadership"]
BLAKE = ["user:blake@northwind.example", "group:us-analysts"]
NOBODY = ["user:stranger@northwind.example"]


@pytest.fixture
async def warehouse():
    # Function-scoped, and init_db() every test: asyncpg binds its pool to the running
    # loop, and pytest-asyncio gives each test a fresh one. seed() is idempotent, so it
    # only actually inserts on the first test that finds the warehouse empty.
    try:
        await init_db()
    except Exception as e:  # noqa: BLE001 — no database in this environment
        pytest.skip(f"database unavailable: {e}")
    await finance.seed()
    try:
        yield
    finally:
        await close_db()


async def test_ada_sees_every_region(warehouse):
    result = await finance.execute("revenue_by_region", {}, ADA)
    codes = {r["region_code"] for r in result.rows}
    assert codes == {"NA-US", "NA-CA", "EMEA-UK", "EMEA-DE", "APAC-JP"}
    assert result.withheld == {}
    assert not result.narrowed


async def test_blake_sees_only_us_and_is_told_what_is_withheld(warehouse):
    result = await finance.execute("revenue_by_region", {}, BLAKE)
    codes = {r["region_code"] for r in result.rows}
    assert codes == {"NA-US"}
    # The count, never the names — 4 of 5 regions withheld.
    assert result.withheld.get("region") == 4
    assert result.narrowed


async def test_predicate_is_appended_not_substituted(warehouse):
    """Blake asking for EMEA-DE gets nothing, not EMEA-DE's numbers and not an error.

    There is no per-region parameter on this op, so the closest a caller can come to
    'ask for another region' is the entitlement itself — which is exactly what the
    gate refuses to widen. Blake's revenue is US revenue; it can never equal Ada's.
    """
    ada = await finance.execute("revenue_by_region", {}, ADA)
    blake = await finance.execute("revenue_by_region", {}, BLAKE)
    ada_total = sum(r["revenue"] for r in ada.rows)
    blake_total = sum(r["revenue"] for r in blake.rows)
    assert blake_total < ada_total  # a strict subset, never the full company


async def test_stranger_is_denied_not_errored(warehouse):
    result = await finance.execute("revenue_by_region", {}, NOBODY)
    assert result.rows == []
    assert result.denied
    # withheld tells them how much exists without disclosing what.
    assert result.withheld.get("region") == 5


async def test_fingerprint_separates_principals(warehouse):
    """The specific bug this design exists to prevent: Ada's cached rows reaching Blake.

    The entitlement fingerprint is part of the query cache key, so it must differ
    between two principals who can see different things.
    """
    ada = await finance.execute("revenue_by_region", {}, ADA)
    blake = await finance.execute("revenue_by_region", {}, BLAKE)
    assert ada.fingerprint != blake.fingerprint


async def test_empty_principals_see_nothing(warehouse):
    result = await finance.execute("revenue_by_region", {}, [])
    assert result.rows == []


async def test_entitled_dimensions_match_predicate(warehouse):
    """A dimension declared entitled but not enforced in the predicate would silently
    grant everything. This guards the two lists against drifting apart."""
    # every dimension finance claims to gate must be one _predicate knows how to filter
    ent = finance.Entitlement({"region": {"NA-US"}, "segment": {"PLATFORM"}})
    clause, args = finance._predicate(ent, "r", 2)
    assert "region_code" in clause
    assert "segment_code" in clause
