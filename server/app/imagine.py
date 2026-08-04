"""Grok Imagine image generation, through the xAI SDK."""

from .config import settings
from .xai import client


async def generate_image(prompt: str, quality: bool = False) -> str:
    model = settings.image_model_quality if quality else settings.image_model
    response = await client().image.sample(prompt=prompt, model=model, image_format="url")
    url = getattr(response, "url", None)
    if not url:
        raise RuntimeError("image generation returned no url")
    return url
