from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./scanner.db"
    anthropic_api_key: str = ""
    scraper_api_key: str = ""


settings = Settings()
