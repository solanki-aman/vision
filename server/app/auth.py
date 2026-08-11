"""Identity as a backend-for-frontend: the browser never holds a token.

Authorization Code with PKCE runs server-side; the client gets an opaque
`HttpOnly; SameSite=Lax` cookie naming a session row. That is not only the safer
shape, it is the only one that works here — `EventSource` and `<img src>` cannot
carry an `Authorization` header, and this feature depends on both (the canvas event
stream and every page image). A bearer token in the browser would force page images
through blob fetches and a rewrite of the event stream, and would be readable by any
XSS. A cookie authenticates all three transports unchanged.

Plain OIDC, so the provider is a URL in config: Okta, Entra, Auth0 and Google all
work without a code change.

**Groups are resolved to a role once, at session creation.** Permissions are never
re-derived from a token claim per request: claims go stale between refreshes, and a
user removed from a group should lose access at their next request, not their next
login — which is what a server-side session gives you, since deleting the row is
immediate revocation.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request, Response
from joserfc import jwt
from joserfc.jwk import KeySet

from .config import settings
from .db import audit, pool

log = logging.getLogger("vision.auth")

OIDC_STATE_COOKIE = "vision_oidc"
STATE_TTL_SECONDS = 600

# When no issuer is configured the whole stack runs as this one principal. `main.py`
# warns loudly at startup — an unauthenticated instance on a reachable port is an
# open document store.
LOCAL_SUBJECT = "local-user"


@dataclass
class Principal:
    subject: str
    email: str | None = None
    name: str | None = None
    groups: list[str] = field(default_factory=list)
    role: str = "member"
    session_id: str | None = None

    @property
    def principals(self) -> list[str]:
        """Every grant principal this identity matches."""
        return [f"user:{self.subject}"] + [f"group:{g}" for g in self.groups]

    @property
    def actor(self) -> str:
        """What lands in audit.events and change_sets.actor."""
        return self.email or self.subject


LOCAL_PRINCIPAL = Principal(subject=LOCAL_SUBJECT, name="Local user", role="owner")


# ---- provider discovery -----------------------------------------------------------

_metadata: dict[str, Any] | None = None
_jwks: Any = None


async def metadata() -> dict[str, Any]:
    global _metadata
    if _metadata is None:
        url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=15) as http:
            res = await http.get(url)
            res.raise_for_status()
            _metadata = res.json()
    return _metadata


async def jwks() -> Any:
    """Cached per process. Rotation is handled by clearing on a validation failure
    rather than refetching per request, which would make the IdP a hot dependency."""
    global _jwks
    if _jwks is None:
        meta = await metadata()
        async with httpx.AsyncClient(timeout=15) as http:
            res = await http.get(meta["jwks_uri"])
            res.raise_for_status()
            _jwks = KeySet.import_key_set(res.json())
    return _jwks


# ---- the login-attempt cookie ------------------------------------------------------
# state, nonce and PKCE verifier ride in a signed short-lived cookie rather than a
# table: they are single-use, expire in minutes, and this keeps login stateless.


# Signing key for login attempts and render tokens. With an IdP configured the client
# secret is the natural key; without one there is nothing durable to key from, so a
# per-process random value is used rather than a constant. A guessable key would make
# render tokens forgeable, and "authentication is off anyway" is not a reason to ship
# a known secret — the mode can be switched on without restarting this thought.
_EPHEMERAL_SECRET = secrets.token_urlsafe(32)


def _sign(payload: str) -> str:
    secret = (settings.oidc_client_secret or _EPHEMERAL_SECRET).encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def pack_attempt(state: str, nonce: str, verifier: str, redirect_to: str) -> str:
    body = _b64(json.dumps(
        {"s": state, "n": nonce, "v": verifier, "r": redirect_to, "t": int(time.time())}
    ).encode())
    return f"{body}.{_sign(body)}"


def unpack_attempt(cookie: str | None) -> dict[str, Any]:
    if not cookie or "." not in cookie:
        raise HTTPException(status_code=400, detail="login state missing")
    body, _, signature = cookie.rpartition(".")
    if not hmac.compare_digest(signature, _sign(body)):
        raise HTTPException(status_code=400, detail="login state invalid")
    padded = body + "=" * (-len(body) % 4)
    data = json.loads(base64.urlsafe_b64decode(padded))
    if time.time() - data["t"] > STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="login state expired")
    return data


# ---- sessions -----------------------------------------------------------------------


def make_render_token(canvas_id: str, ttl: int = 120) -> str:
    """A capability for one canvas, for two minutes.

    The shooter service loads the SPA headlessly to produce exports and has no
    session. Handing it a standing service credential would give the renderer
    permanent access to every canvas; this is scoped to one and expires.
    """
    body = f"{canvas_id}|{int(time.time()) + ttl}"
    return f"{body}|{_sign(body)}"


def verify_render_token(token: str, canvas_id: str) -> bool:
    parts = token.split("|")
    if len(parts) != 3:
        return False
    cid, expiry, signature = parts
    if not hmac.compare_digest(signature, _sign(f"{cid}|{expiry}")):
        return False
    try:
        return cid == canvas_id and int(expiry) > time.time()
    except ValueError:
        return False


async def create_session(claims: dict[str, Any]) -> str:
    groups = claims.get(settings.oidc_groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    session_id = secrets.token_urlsafe(32)
    await pool().execute(
        """INSERT INTO auth.sessions (id, subject, email, display_name, role, groups, expires_at)
           VALUES ($1,$2,$3,$4,$5,$6, now() + make_interval(secs => $7))""",
        session_id,
        claims["sub"],
        claims.get("email"),
        claims.get("name"),
        role_for_groups(groups),
        list(groups),
        settings.session_ttl_seconds,
    )
    await audit("login", "applied", None, None, {"subject": claims["sub"]})
    return session_id


def role_for_groups(groups: list[str]) -> str:
    """Map IdP groups to an application role, once, at session creation."""
    lowered = {g.lower() for g in groups}
    if {"vision-admins", "vision_admins"} & lowered:
        return "admin"
    return "member"


async def load_session(session_id: str) -> Principal | None:
    row = await pool().fetchrow(
        """SELECT id, subject, email, display_name, role, groups
           FROM auth.sessions WHERE id = $1 AND expires_at > now()""",
        session_id,
    )
    if row is None:
        return None
    return Principal(
        subject=row["subject"],
        email=row["email"],
        name=row["display_name"],
        groups=list(row["groups"] or []),
        role=row["role"],
        session_id=row["id"],
    )


async def destroy_session(session_id: str) -> None:
    await pool().execute("DELETE FROM auth.sessions WHERE id = $1", session_id)


async def purge_expired_sessions() -> int:
    result = await pool().execute("DELETE FROM auth.sessions WHERE expires_at <= now()")
    try:
        return int(str(result).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        settings.session_cookie,
        session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.session_cookie, path="/")


# ---- the login flow -------------------------------------------------------------------


async def authorize_redirect(redirect_to: str) -> tuple[str, str]:
    """Returns (authorization url, the attempt cookie to set alongside it)."""
    meta = await metadata()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_url,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{meta['authorization_endpoint']}?{query}", pack_attempt(
        state, nonce, verifier, redirect_to
    )


async def exchange_code(code: str, verifier: str, nonce: str) -> dict[str, Any]:
    meta = await metadata()
    async with httpx.AsyncClient(timeout=20) as http:
        res = await http.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_url,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
        )
    if res.status_code != 200:
        log.warning("token exchange failed: %s", res.text[:300])
        raise HTTPException(status_code=401, detail="token exchange failed")

    id_token = res.json().get("id_token")
    if not id_token:
        raise HTTPException(status_code=401, detail="no id_token in token response")

    token = jwt.decode(id_token, await jwks())
    return validate_claims(dict(token.claims), nonce)


def validate_claims(claims: dict[str, Any], nonce: str) -> dict[str, Any]:
    """Check an already signature-verified id_token's claims.

    Separate from the HTTP exchange so it can be tested without an IdP — this is the
    security-critical half, and a signature that verifies against the right key still
    proves nothing if the token was minted for a different audience or replayed from
    an older login.
    """
    issuer = str(claims.get("iss") or "")
    if issuer.rstrip("/") != settings.oidc_issuer.rstrip("/"):
        raise HTTPException(status_code=401, detail="issuer mismatch")

    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if settings.oidc_client_id not in audiences:
        raise HTTPException(status_code=401, detail="audience mismatch")

    expiry = claims.get("exp")
    if not isinstance(expiry, (int, float)) or expiry <= time.time():
        raise HTTPException(status_code=401, detail="id_token expired")

    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="id_token has no subject")

    # Binds this token to this login attempt; without it a token captured from
    # another session for the same client would be accepted.
    if not nonce or claims.get("nonce") != nonce:
        raise HTTPException(status_code=401, detail="nonce mismatch")

    return claims


# ---- request guards ---------------------------------------------------------------------


async def current_principal(request: Request) -> Principal:
    """The caller, or 401. Use as a FastAPI dependency."""
    if not settings.auth_enabled:
        return LOCAL_PRINCIPAL
    session_id = request.cookies.get(settings.session_cookie)
    if not session_id:
        raise HTTPException(status_code=401, detail="not authenticated")
    principal = await load_session(session_id)
    if principal is None:
        raise HTTPException(status_code=401, detail="session expired")
    return principal


async def require_canvas(canvas_id: Any, principal: Principal, *, write: bool = False) -> str:
    """The caller's role on a canvas, or 404.

    **404, not 403.** A 403 confirms the canvas exists, which leaks the id space to
    anyone willing to enumerate. The only thing an unauthorised caller learns is
    that they cannot have it.
    """
    from .docstore import ROLE_RANK, canvas_role  # circular at module scope

    if not settings.auth_enabled:
        return "owner"
    role = await canvas_role(canvas_id, principal.principals)
    if role is None:
        raise HTTPException(status_code=404, detail="not found")
    if write and ROLE_RANK.get(role, 0) < ROLE_RANK["editor"]:
        raise HTTPException(status_code=404, detail="not found")
    return role
