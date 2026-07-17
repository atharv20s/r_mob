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
    
    # AI Settings — Cloud Providers
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    MISTRAL_API_KEY: Optional[str] = None
    
    # AI Settings — Local Inference (comma-separated for multi-node clusters)
    OLLAMA_BASE_URL: str = "http://localhost:11434"          # backward compat
    OLLAMA_NODES: str = "http://localhost:11434"              # comma-separated
    VLLM_BASE_URL: str = ""                                  # backward compat
    VLLM_NODES: str = ""                                     # comma-separated
    DEFAULT_PROVIDER: str = "ollama"
    DEFAULT_MODEL: str = "mistral:latest"                     # Ollama-first
    FALLBACK_PROVIDERS: str = ""                             # no fallback
    HEALTH_CHECK_INTERVAL: int = 30                           # seconds
    
    # Security
    PROMPT_SAFETY_ENABLED: bool = True
    
    # Observability
    METRICS_ENABLED: bool = True
    LOG_FORMAT: str = "json"   # "json" for production, "text" for dev
    LOG_LEVEL: str = "INFO"
    
    # Cache & Persistence
    DATABASE_URL: str = "sqlite:///./route_mobile.db"
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_REQUIRED: bool = True
    GLOBAL_CACHE_TTL: int = 3600        # 1 hour — shared across all users
    CONVERSATION_CACHE_TTL: int = 600   # 10 min — per-conversation
    
    # 6-Tier Rate Limiting
    GATEWAY_RPS_LIMIT: int = 10000      # global gateway cap
    IP_RPS_LIMIT: int = 100             # per-IP DDoS protection
    MODEL_RATE_LIMITS: str = '{}'       # JSON: {"gemma2:2b": 50, "gpt-4o": 20}
    
    # JWT Authentication Settings
    JWT_SECRET: str = "default_secret_key_change_me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Multi-Tenancy
    DEFAULT_ORG_NAME: str = "Personal"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
