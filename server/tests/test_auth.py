"""The parts of the login flow that can be tested without an identity provider.

Signature verification is joserfc's job; everything here is the code that decides
whether a *validly signed* token should be trusted, plus the two secrets this service
mints itself — the login-attempt cookie and the renderer's capability token. Those
are small, security-critical, and entirely ours, so they get pinned.
"""

import time

import pytest
from fastapi import HTTPException
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

import app.auth as auth
from app.auth import (
    Principal,
    make_render_token,
    pack_attempt,
    role_for_groups,
    unpack_attempt,
    validate_claims,
    verify_render_token,
)

ISSUER = "https://idp.example.com"
CLIENT = "vision-client"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(auth.settings, "oidc_issuer", ISSUER, raising=False)
    monkeypatch.setattr(auth.settings, "oidc_client_id", CLIENT, raising=False)
    monkeypatch.setattr(auth.settings, "oidc_client_secret", "s3cret", raising=False)


def claims(**overrides):
    base = {
        "sub": "00u123",
        "iss": ISSUER,
        "aud": CLIENT,
        "nonce": "nonce-1",
        "exp": int(time.time()) + 300,
        "email": "a@example.com",
    }
    base.update(overrides)
    return base


# ---- id_token claims ---------------------------------------------------------------


def test_a_well_formed_token_is_accepted():
    assert validate_claims(claims(), "nonce-1")["sub"] == "00u123"


def test_a_trailing_slash_on_the_issuer_is_not_a_mismatch():
    assert validate_claims(claims(iss=ISSUER + "/"), "nonce-1")


@pytest.mark.parametrize(
    "override,message",
    [
        ({"iss": "https://evil.example.com"}, "issuer"),
        ({"aud": "some-other-client"}, "audience"),
        ({"exp": int(time.time()) - 1}, "expired"),
        ({"exp": None}, "expired"),
        ({"sub": ""}, "subject"),
    ],
)
def test_bad_claims_are_rejected(override, message):
    with pytest.raises(HTTPException) as e:
        validate_claims(claims(**override), "nonce-1")
    assert e.value.status_code == 401 and message in e.value.detail


def test_a_replayed_token_from_another_login_is_rejected():
    """The signature is fine and the audience is ours — only the nonce says this
    token belongs to a different login attempt."""
    with pytest.raises(HTTPException, match="nonce"):
        validate_claims(claims(nonce="someone-elses"), "nonce-1")


def test_a_missing_nonce_never_passes():
    with pytest.raises(HTTPException, match="nonce"):
        validate_claims(claims(nonce=None), "")


def test_an_audience_array_containing_us_is_accepted():
    assert validate_claims(claims(aud=["other", CLIENT]), "nonce-1")


def test_a_real_signed_token_round_trips_through_decode_and_validation():
    """End-to-end over the actual crypto path, with a locally generated key."""
    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    encoded = jwt.encode({"alg": "RS256", "kid": "k1"}, claims(), key)
    decoded = jwt.decode(encoded, KeySet([key]))
    assert validate_claims(dict(decoded.claims), "nonce-1")["email"] == "a@example.com"


def test_a_token_signed_by_the_wrong_key_does_not_verify():
    ours = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    theirs = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    forged = jwt.encode({"alg": "RS256", "kid": "k1"}, claims(), theirs)
    with pytest.raises(Exception):
        jwt.decode(forged, KeySet([ours]))


# ---- the login-attempt cookie -----------------------------------------------------


def test_attempt_cookie_round_trips():
    cookie = pack_attempt("state-1", "nonce-1", "verifier-1", "/canvas/7")
    data = unpack_attempt(cookie)
    assert (data["s"], data["n"], data["v"], data["r"]) == (
        "state-1", "nonce-1", "verifier-1", "/canvas/7",
    )


def test_a_tampered_attempt_cookie_is_rejected():
    cookie = pack_attempt("state-1", "nonce-1", "verifier-1", "/")
    body, _, signature = cookie.rpartition(".")
    with pytest.raises(HTTPException, match="invalid"):
        unpack_attempt(f"{body}x.{signature}")


def test_an_unsigned_attempt_cookie_is_rejected():
    with pytest.raises(HTTPException):
        unpack_attempt("just-some-text")
    with pytest.raises(HTTPException, match="missing"):
        unpack_attempt(None)


def test_a_stale_attempt_cookie_is_rejected(monkeypatch):
    cookie = pack_attempt("s", "n", "v", "/")
    later = time.time() + auth.STATE_TTL_SECONDS + 5  # captured before patching time
    monkeypatch.setattr(auth.time, "time", lambda: later)
    with pytest.raises(HTTPException, match="expired"):
        unpack_attempt(cookie)


# ---- the renderer's capability token -------------------------------------------------


def test_render_token_admits_only_its_own_canvas():
    token = make_render_token("canvas-a")
    assert verify_render_token(token, "canvas-a")
    assert not verify_render_token(token, "canvas-b")


def test_render_token_expires():
    assert not verify_render_token(make_render_token("canvas-a", ttl=-1), "canvas-a")


@pytest.mark.parametrize("bad", ["", "nonsense", "a|b", "canvas-a|9999999999|deadbeef"])
def test_a_forged_render_token_is_rejected(bad):
    assert not verify_render_token(bad, "canvas-a")


def test_render_tokens_are_not_signed_with_a_guessable_key(monkeypatch):
    """With no IdP configured there is no client secret to sign with. It must fall
    back to a per-process random value, not a constant — a known key makes every
    render token forgeable by anyone who can read this source."""
    monkeypatch.setattr(auth.settings, "oidc_client_secret", "", raising=False)
    monkeypatch.setattr(auth.settings, "oidc_client_id", "", raising=False)
    assert auth._sign("x") != auth.hmac.new(
        b"vision", b"x", auth.hashlib.sha256
    ).hexdigest()


# ---- principals and roles ---------------------------------------------------------------


def test_a_principal_matches_user_and_group_grants():
    p = Principal(subject="00u1", groups=["g-analysts", "g-all"])
    assert p.principals == ["user:00u1", "group:g-analysts", "group:g-all"]


def test_actor_prefers_email_so_the_audit_trail_names_a_person():
    assert Principal(subject="00u1", email="a@example.com").actor == "a@example.com"
    assert Principal(subject="00u1").actor == "00u1"


@pytest.mark.parametrize(
    "groups,expected",
    [(["vision-admins"], "admin"), (["Vision_Admins"], "admin"), (["everyone"], "member"), ([], "member")],
)
def test_groups_resolve_to_a_role(groups, expected):
    assert role_for_groups(groups) == expected
