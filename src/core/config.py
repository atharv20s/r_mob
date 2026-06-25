from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "Route Mobile API"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = True
    
    # AWS Settings
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "us-east-1"
    AWS_BUCKET_NAME: Optional[str] = None
    
    # AI Settings
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    DEFAULT_MODEL: str = "gemini-1.5-flash"
    
    # Mistral Settings
    MISTRAL_API_KEY: Optional[str] = None
    
    # Cache & Persistence
    DATABASE_URL: str = "sqlite:///./route_mobile.db"
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_REQUIRED: bool = True
    
    # JWT Authentication Settings
    JWT_SECRET: str = "default_secret_key_change_me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
