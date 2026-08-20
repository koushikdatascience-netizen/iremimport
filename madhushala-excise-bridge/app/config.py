"""
Configuration for Madhushala Excise Bridge
"""
import os
from typing import Optional
from pydantic import BaseSettings

class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Madhushala API Configuration
    MADHUSHALA_BASE_URL: str = "https://reportapi.madhushalasoftware.com"
    MADHUSHALA_SHOP_CODE: str = "hedu_test3"
    MADHUSHALA_TOKEN: str = ""
    
    # Local Server Configuration
    LOCAL_HOST: str = "127.0.0.1"
    LOCAL_PORT: int = 8091
    
    # Browser Configuration
    HEADLESS: bool = False
    
    # Database Configuration
    DATABASE_URL: str = "sqlite:///data/excise_bridge.db"
    
    # CORS Configuration
    CORS_ORIGINS: list = ["http://localhost:8091", "http://127.0.0.1:8091"]
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()