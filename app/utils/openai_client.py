"""Shared synchronous OpenAI client instance."""

from openai import OpenAI

from app.config import settings

# Single shared client — reuses the underlying HTTP connection pool
openai_client = OpenAI(api_key=settings.ai_api_key)
