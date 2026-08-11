"""Process configuration. Everything the service needs comes from the environment."""

import os


class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgres://vision:vision@localhost:5433/vision"
    )
    xai_api_key: str = os.getenv("XAI_API_KEY", "")
    xai_model: str = os.getenv("XAI_MODEL") or "grok-4.5"
    # Reasoning cost is paid on EVERY step, and a build turn is many steps, so the
    # effort level is the biggest latency lever we have. "low" keeps the model's
    # judgment (chart choice, thesis, layout) while cutting per-step thinking time.
    # `or` rather than a getenv default: compose passes unset variables through as
    # an empty string, which getenv reports as set — an empty model name reaches
    # the API as "Model not found: ".
    xai_reasoning_effort: str = os.getenv("XAI_REASONING_EFFORT") or "low"
    # Search only extracts figures from pages someone else wrote — it needs accuracy,
    # not deliberation, so it can run on a cheaper/faster model than the composer.
    search_model: str = os.getenv("XAI_SEARCH_MODEL") or os.getenv("XAI_MODEL") or "grok-4.5"
    image_model: str = os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image")
    image_model_quality: str = os.getenv(
        "XAI_IMAGE_MODEL_QUALITY", "grok-imagine-image-quality"
    )
    port: int = int(os.getenv("PORT", "3001"))
    # LangSmith reads LANGSMITH_* from the environment itself; these are here so the
    # service can report whether tracing is on and under which project.
    langsmith_tracing: bool = os.getenv("LANGSMITH_TRACING", "").lower() in ("1", "true", "yes")
    langsmith_project: str = os.getenv("LANGSMITH_PROJECT", "vision")
    # The headless renderer used for canvas exports (and the agent's own look at its work).
    shooter_url: str = os.getenv("SHOOTER_URL", "http://shooter:3002")
    # A building turn can fan out across many tool calls; cap it like the prototype did.
    max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "16"))

    # ---- documents ---------------------------------------------------------------
    # Uploaded files are rasterised and read as images; see app/documents.py for why,
    # and for the measurements these defaults come from.
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT") or "minio:9000"
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY") or "vision"
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY") or "vision-secret"
    minio_bucket: str = os.getenv("MINIO_BUCKET") or "documents"
    minio_secure: bool = (os.getenv("MINIO_SECURE") or "").lower() in ("1", "true", "yes")

    # Images may occupy at most this much of a turn. Chosen against grok-4.5's 500k
    # window and its price step at 200k: the skill (~7k), canvas summary,
    # conversation and tool traffic all have to fit alongside and stay under it.
    doc_image_budget: int = int(os.getenv("DOC_IMAGE_BUDGET") or "120000")
    # Measured floors, not preferences. 36 dpi produced confident wrong digits;
    # 45 was exact on sparse pages, dense 9pt pages need ~62 in a 4-up, 110 solo.
    doc_page_dpi: int = int(os.getenv("DOC_PAGE_DPI") or "110")
    doc_sheet_dpi: int = int(os.getenv("DOC_SHEET_DPI") or "62")
    doc_sheet_cols: int = int(os.getenv("DOC_SHEET_COLS") or "2")
    # Humans read at a higher resolution than the model needs; this one is only ever
    # served to a browser, never to the API.
    doc_read_dpi: int = int(os.getenv("DOC_READ_DPI") or "150")
    # A 5,000-page PDF is a denial-of-service against the render budget.
    doc_max_bytes: int = int(os.getenv("DOC_MAX_BYTES") or str(50 * 1024 * 1024))
    doc_max_pages: int = int(os.getenv("DOC_MAX_PAGES") or "1500")
    # Page images from the two most recent tool results stay materialised; older ones
    # become text stubs, so a long ReAct loop cannot accumulate megabytes of pixels.
    doc_window_steps: int = int(os.getenv("DOC_WINDOW_STEPS") or "2")

    # A credentialed request cannot use a wildcard origin, and the session cookie is
    # the whole authentication story — so origins are named rather than "*".
    cors_origins: list[str] = [
        o.strip()
        for o in (
            os.getenv("CORS_ORIGINS") or "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if o.strip()
    ]

    # ---- identity ----------------------------------------------------------------
    # Plain OIDC, so the provider is a URL: Okta, Entra, Auth0 and Google all work
    # unchanged. Setting OIDC_ISSUER is what turns authentication on.
    oidc_issuer: str = os.getenv("OIDC_ISSUER") or ""
    oidc_client_id: str = os.getenv("OIDC_CLIENT_ID") or ""
    oidc_client_secret: str = os.getenv("OIDC_CLIENT_SECRET") or ""
    oidc_redirect_url: str = os.getenv("OIDC_REDIRECT_URL") or "http://localhost:3001/api/auth/callback"
    oidc_post_logout_url: str = os.getenv("OIDC_POST_LOGOUT_URL") or "http://localhost:5173/"
    # Claim carrying group membership; groups are resolved to a role once, at session
    # creation, never re-derived per request from a claim that may have gone stale.
    oidc_groups_claim: str = os.getenv("OIDC_GROUPS_CLAIM") or "groups"
    # Canvases created before grants existed have no owner, so deny-by-default
    # correctly refuses them the moment authentication is switched on. Setting this
    # to a subject claim gives those canvases an owner at startup, once.
    auth_bootstrap_subject: str = os.getenv("AUTH_BOOTSTRAP_SUBJECT") or ""
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS") or str(8 * 3600))
    session_cookie: str = os.getenv("SESSION_COOKIE") or "vision_session"
    # Cookies are only marked Secure over TLS; leaving this off on localhost is what
    # lets the flow work at all during development.
    session_cookie_secure: bool = (os.getenv("SESSION_COOKIE_SECURE") or "").lower() in (
        "1",
        "true",
        "yes",
    )

    @property
    def auth_enabled(self) -> bool:
        """Authentication is on exactly when an issuer is configured.

        With it off every request runs as a single local principal, which is the only
        way this stack runs on a laptop without an IdP. `main.py` logs a warning at
        startup and `/api/health` reports the mode, because an unauthenticated
        instance reachable from a network is an open document store.
        """
        return bool(self.oidc_issuer and self.oidc_client_id)


settings = Settings()
