"""Provider-agnostic client dispatcher.

Resolves the correct client instance from the model registry at call time.
Currently only the 'openai' provider is implemented — adding a new provider
means registering it in EMBEDDING_MODELS / LLM_MODELS and adding a branch here.
"""

from app.config import EMBEDDING_MODELS, LLM_MODELS
from app.utils.openai_client import openai_client


def get_client(model: str):
    """Return the client for the given model's provider.

    Looks up the model in the EMBEDDING_MODELS and LLM_MODELS registries to
    determine the provider, then returns the matching client instance.
    Raises NotImplementedError for providers that are not yet wired up.
    """
    provider = (
        EMBEDDING_MODELS.get(model) or LLM_MODELS.get(model) or {}
    ).get("provider", "openai")

    if provider == "openai":
        return openai_client

    raise NotImplementedError(f"Provider '{provider}' is not yet implemented")
