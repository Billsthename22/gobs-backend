
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    FRONTEND_URL: str = "http://localhost:3000"

    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
