"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Model registry
#
# Maps model IDs to their key parameters so chunking and embedding logic can
# adapt to the selected model without hardcoded per-model branches.
#
# chunk_size_chars: target (baseline + check combined) per LLM prompt.
# ---------------------------------------------------------------------------

EMBEDDING_MODELS: dict[str, dict] = {
    "text-embedding-3-small": {"provider": "openai", "max_tokens": 8_191, "dimensions": 1_536},
    "text-embedding-3-large": {"provider": "openai", "max_tokens": 8_191, "dimensions": 3_072},
    "all-MiniLM-L6-v2":       {"provider": "huggingface", "max_tokens": 512, "dimensions": 384},
}

LLM_MODELS: dict[str, dict] = {
    "gpt-4o-mini":    {"provider": "openai",     "context_window": 128_000, "chunk_size_chars": 6_000},
    "gpt-4-turbo":    {"provider": "openai",     "context_window": 128_000, "chunk_size_chars": 4_000},
    "claude-3-haiku": {"provider": "anthropic",  "context_window": 200_000, "chunk_size_chars": 5_000},
}


class Settings(BaseSettings):
    """All application settings, configurable via environment variables or .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database (psycopg2 sync driver)
    database_url: str = "postgresql+psycopg2://wpim:wpim@localhost:5432/wpim"

    # RabbitMQ (task queue for async baseline acquisition + checks)
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"

    # AI provider API key (OpenAI, Anthropic, etc.)
    ai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"

    # Scheduler: interval in seconds between polling cycles
    scheduler_interval: int = 60

    # Defaults applied to newly created URLs
    default_frequency: int = 3600
    default_diff_threshold_ok: float = 5.0
    default_diff_threshold_alert: float = 50.0
    default_cosine_threshold_ok: float = 0.95
    default_cosine_threshold_alert: float = 0.5

    # LLM chunking — controls how long texts are split before level-3 analysis
    llm_chunk_max_chars: int = 6000   # max chars (baseline + check combined) per LLM prompt
    llm_context_chars: int = 300      # padding kept around each diff block
    llm_chunk_overlap_chars: int = 200  # overlap between windows when splitting long blocks
    llm_merge_gap_chars: int = 500    # merge adjacent diff blocks closer than this

    # HTTP fetch timeout in seconds
    fetch_timeout: int = 30

    # Disable SSL verification (useful in corporate/proxy environments)
    fetch_verify_ssl: bool = True

    # Logging level (DEBUG, INFO, WARNING, ERROR)
    log_level: str = "INFO"


settings = Settings()
