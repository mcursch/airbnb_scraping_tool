from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str = ""
    scraper_api_key: str = ""
    database_url: str = "sqlite:///scanner.db"
    extraction_model: str = "claude-opus-4-8"
    # Use Message Batches API when a run has more records than this threshold
    batch_threshold: int = 20


settings = Settings()
