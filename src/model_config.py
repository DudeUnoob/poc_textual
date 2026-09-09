"""Single source for new extraction defaults; historical runs retain their config."""
import os

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
DEFAULT_THINKING_LEVEL = os.environ.get("GEMINI_THINKING_LEVEL", "medium").lower()
DEFAULT_MAX_OUTPUT_TOKENS = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "16384"))


def thinking_level(value: str | None = None) -> str:
    level = (value or DEFAULT_THINKING_LEVEL).lower()
    if level not in {"low", "medium", "high"}:
        raise ValueError("GEMINI_THINKING_LEVEL must be low, medium, or high")
    return level
