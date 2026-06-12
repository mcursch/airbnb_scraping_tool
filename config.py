from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # claude-opus-4-8 per-token prices in USD per million tokens (MTok)
    CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK: float = 15.0
    CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK: float = 75.0
    CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK: float = 1.50

    DATABASE_URL: str = "sqlite:///./scanner.db"


settings = Settings()
