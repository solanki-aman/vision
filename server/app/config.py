"""Process configuration. Everything the service needs comes from the environment."""

import os


class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgres://vision:vision@localhost:5433/vision"
    )
    xai_api_key: str = os.getenv("XAI_API_KEY", "")
    xai_model: str = os.getenv("XAI_MODEL", "grok-4.5")
    image_model: str = os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image")
    image_model_quality: str = os.getenv(
        "XAI_IMAGE_MODEL_QUALITY", "grok-imagine-image-quality"
    )
    port: int = int(os.getenv("PORT", "3001"))
    # A building turn can fan out across many tool calls; cap it like the prototype did.
    max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "16"))


settings = Settings()
