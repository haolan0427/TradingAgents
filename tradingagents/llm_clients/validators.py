"""Model name validators for each provider."""

from .model_catalog import get_known_models


VALID_MODELS = get_known_models()


def validate_model(provider: str, model: str) -> bool:
    """Check if model name is valid for the given provider."""
    provider_lower = provider.lower()

    if provider_lower not in VALID_MODELS:
        return True

    return model in VALID_MODELS[provider_lower]
    return PROVIDER_API_KEY_ENV.get(provider.lower())
