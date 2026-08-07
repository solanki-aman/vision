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


settings = Settings()
