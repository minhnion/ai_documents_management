from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    APP_NAME: str = "Guideline Management Platform API"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    DEBUG: bool = True

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DB_HOST: str = "localhost"
    DB_PORT: int = 5436
    DB_USER: str = "phuongnh"
    DB_PASSWORD: str = "1234"
    DB_NAME: str = "chatbot_healthcare"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    LOCAL_STORAGE_ROOT: str = "uploads"
    SCORE_THRESHOLD: float = 0.65
    CHUNK_MAX_CHARS: int = 3000

    # API
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: list[str] = ["*"]

    # External AI services
    LANDINGAI_API_KEY: str = ""
    LANDINGAI_API_URL: str = "https://api.va.landing.ai/v1/ade/parse"
    LANDINGAI_MODEL_NAME: str = ""
    LANDINGAI_USE_SDK: bool = True
    OPENAI_API_KEY: str = ""
    OPENAI_API_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL_NAME: str = ""
    OPENAI_EMBEDDING_MODEL_NAME: str = "text-embedding-3-large"
    CORE_AI_BASE_URL: str = ""
    CORE_AI_API_KEY: str = ""

    # Auth / Security
    JWT_SECRET_KEY: str = "change-this-secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    AUTO_CREATE_TABLES: bool = True
    SEED_AUTH_DATA: bool = True
    DEFAULT_ADMIN_EMAIL: str = "admin@example.com"
    DEFAULT_ADMIN_PASSWORD: str = "ChangeMe123!"
    DEFAULT_ADMIN_FULL_NAME: str = "System Admin"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync URL (used for Alembic migrations)."""
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
