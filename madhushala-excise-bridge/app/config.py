"""Configuration for Madhushala Excise Bridge."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Local settings object.

    Secrets are read from environment variables and must not be committed.
    """

    LOCAL_HOST: str = os.getenv("LOCAL_HOST", "127.0.0.1")
    LOCAL_PORT: int = int(os.getenv("LOCAL_PORT", "8091"))
    HEADLESS: bool = _env_bool("HEADLESS", False)
    EXCISE_LOGIN_URL: str = os.getenv(
        "EXCISE_LOGIN_URL",
        "https://excise.wb.gov.in/WBSBCL/Bevco/NIC/UserLogin/Login.aspx",
    )
    BROWSER_PROFILE_DIR: str = os.getenv("BROWSER_PROFILE_DIR", "data/browser_profile")
    CAPTURES_DIR: str = os.getenv("CAPTURES_DIR", "data/captures")
    MAPPINGS_DIR: str = os.getenv("MAPPINGS_DIR", "data/mappings")
    MADHUSHALA_BASE_URL: str = os.getenv(
        "MADHUSHALA_BASE_URL",
        "https://reportapi.madhushalasoftware.com",
    )
    MADHUSHALA_SHOP_CODE: str = os.getenv("MADHUSHALA_SHOP_CODE", "hedu_test3")
    MADHUSHALA_COMPANY_CODE: str = os.getenv("MADHUSHALA_COMPANY_CODE", "2")
    MADHUSHALA_BILL_TYPE: str = os.getenv("MADHUSHALA_BILL_TYPE", "AI")
    DEFAULT_COMPANY_CODE: str = os.getenv("DEFAULT_COMPANY_CODE", os.getenv("MADHUSHALA_COMPANY_CODE", "2"))
    DEFAULT_BILL_TYPE: str = os.getenv("DEFAULT_BILL_TYPE", os.getenv("MADHUSHALA_BILL_TYPE", "AI"))
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "https://integrations.madhushalasoftware.com/excise-import").rstrip("/")
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/bridge.db")
    SESSION_TTL_MINUTES: int = int(os.getenv("SESSION_TTL_MINUTES", "60"))
    SESSION_ENCRYPTION_KEY: str = os.getenv("SESSION_ENCRYPTION_KEY", "")
    CRM_INTEGRATION_KEY: str = os.getenv("CRM_INTEGRATION_KEY", "")
    MADHUSHALA_SERVICE_TOKEN: str = os.getenv("MADHUSHALA_SERVICE_TOKEN", "")
    VALIDATE_MADHUSHALA_TOKEN_ON_SESSION: bool = _env_bool("VALIDATE_MADHUSHALA_TOKEN_ON_SESSION", False)

    # Madhushala HTTP integration. One pooled async client is reused by every
    # request; GET retries are deliberately bounded and purchase/save is never
    # blindly retried because it can create duplicate accounting transactions.
    MADHUSHALA_CONNECT_TIMEOUT_SECONDS: float = float(os.getenv("MADHUSHALA_CONNECT_TIMEOUT_SECONDS", "5"))
    MADHUSHALA_READ_TIMEOUT_SECONDS: float = float(os.getenv("MADHUSHALA_READ_TIMEOUT_SECONDS", "30"))
    MADHUSHALA_WRITE_TIMEOUT_SECONDS: float = float(os.getenv("MADHUSHALA_WRITE_TIMEOUT_SECONDS", "30"))
    MADHUSHALA_POOL_TIMEOUT_SECONDS: float = float(os.getenv("MADHUSHALA_POOL_TIMEOUT_SECONDS", "5"))
    MADHUSHALA_MAX_CONNECTIONS: int = int(os.getenv("MADHUSHALA_MAX_CONNECTIONS", "100"))
    MADHUSHALA_MAX_KEEPALIVE_CONNECTIONS: int = int(os.getenv("MADHUSHALA_MAX_KEEPALIVE_CONNECTIONS", "20"))
    MADHUSHALA_GET_RETRIES: int = int(os.getenv("MADHUSHALA_GET_RETRIES", "2"))
    MADHUSHALA_ITEM_FETCH_CONCURRENCY: int = int(os.getenv("MADHUSHALA_ITEM_FETCH_CONCURRENCY", "8"))

    # Optional shared cache. With REDIS_URL unset the application uses a
    # process-local TTL cache, so current deployments continue to work without
    # infrastructure changes. Supplying REDIS_URL later makes the same code
    # horizontally scalable without changing the purchase flow.
    REDIS_URL: str = os.getenv("REDIS_URL", "").strip()
    CACHE_PREFIX: str = os.getenv("CACHE_PREFIX", "madhushala-bridge:v1")
    CACHE_MASTER_TTL_SECONDS: int = int(os.getenv("CACHE_MASTER_TTL_SECONDS", "1800"))
    CACHE_USER_TTL_SECONDS: int = int(os.getenv("CACHE_USER_TTL_SECONDS", "900"))
    CACHE_SCHEME_TTL_SECONDS: int = int(os.getenv("CACHE_SCHEME_TTL_SECONDS", "900"))
    CACHE_TAX_TTL_SECONDS: int = int(os.getenv("CACHE_TAX_TTL_SECONDS", "10800"))
    CACHE_ITEM_TTL_SECONDS: int = int(os.getenv("CACHE_ITEM_TTL_SECONDS", "21600"))

    # Purchase orchestration controls. Calculation is now the authoritative
    # Madhushala business-rule boundary. Duplicate checking remains best-effort
    # until the upstream team documents a strict response contract.
    PURCHASE_CALCULATION_REQUIRED: bool = _env_bool("PURCHASE_CALCULATION_REQUIRED", True)
    PURCHASE_DUPLICATE_CHECK_REQUIRED: bool = _env_bool("PURCHASE_DUPLICATE_CHECK_REQUIRED", False)
    PURCHASE_DEFAULT_SALES_TAX_RATE: float = float(os.getenv("PURCHASE_DEFAULT_SALES_TAX_RATE", "0"))
    PURCHASE_DEFAULT_SALES_TAX_INCLUDING_FREE: bool = _env_bool("PURCHASE_DEFAULT_SALES_TAX_INCLUDING_FREE", False)

    LLAMA_CLOUD_API_KEY: str = os.getenv("LLAMA_CLOUD_API_KEY", "")
    LLAMA_CLOUD_BASE_URL: str = os.getenv("LLAMA_CLOUD_BASE_URL", "https://api.cloud.llamaindex.ai").rstrip("/")
    DOCUMENT_IMPORT_EXTRACTION_MODE: str = os.getenv("DOCUMENT_IMPORT_EXTRACTION_MODE", "FAST").strip().upper() or "FAST"
    DOCUMENT_IMPORT_POLL_SECONDS: float = float(os.getenv("DOCUMENT_IMPORT_POLL_SECONDS", "1"))
    DOCUMENT_IMPORT_MAX_MB: int = int(os.getenv("DOCUMENT_IMPORT_MAX_MB", "20"))
    DOCUMENT_IMPORT_ALLOWED_TYPES: list[str] = field(
        default_factory=lambda: _env_list("DOCUMENT_IMPORT_ALLOWED_TYPES", ["pdf", "jpg", "jpeg", "png"])
    )
    DOCUMENT_IMPORT_DEBUG: bool = _env_bool("DOCUMENT_IMPORT_DEBUG", False)
    CORS_ORIGINS: list[str] = field(
        default_factory=lambda: _env_list(
            "CORS_ORIGINS",
            [
                "http://localhost:8091",
                "http://127.0.0.1:8091",
                "http://localhost:4200",
                "http://127.0.0.1:4200",
                "https://report.madhushalasoftware.com",
            ],
        )
    )
    CORS_ORIGIN_REGEX: str | None = os.getenv("CORS_ORIGIN_REGEX", r"chrome-extension://.*")

    @property
    def MADHUSHALA_TOKEN(self) -> str:
        return os.getenv("MADHUSHALA_TOKEN", "")

    @property
    def EXCISE_USERNAME(self) -> str:
        return os.getenv("EXCISE_USERNAME", "")

    @property
    def EXCISE_PASSWORD(self) -> str:
        return os.getenv("EXCISE_PASSWORD", "")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().casefold() in {"production", "prod"}

    def validate_production(self) -> None:
        if not self.is_production:
            return
        missing = []
        if not self.APP_BASE_URL.startswith("https://"):
            missing.append("APP_BASE_URL")
        if len(self.CRM_INTEGRATION_KEY) < 32:
            missing.append("CRM_INTEGRATION_KEY")
        if not self.SESSION_ENCRYPTION_KEY:
            missing.append("SESSION_ENCRYPTION_KEY")
        if not self.LLAMA_CLOUD_API_KEY:
            missing.append("LLAMA_CLOUD_API_KEY")
        if self.CORS_ORIGINS == ["*"]:
            missing.append("CORS_ORIGINS")
        if missing:
            raise RuntimeError(f"Missing production settings: {', '.join(missing)}")

settings = Settings()
