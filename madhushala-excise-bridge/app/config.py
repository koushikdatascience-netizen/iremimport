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
    ALLOW_RUNTIME_TOKEN_CONFIG: bool = _env_bool("ALLOW_RUNTIME_TOKEN_CONFIG", False)
    CORS_ORIGINS: list[str] = field(
        default_factory=lambda: _env_list(
            "CORS_ORIGINS",
            ["http://localhost:8091", "http://127.0.0.1:8091"],
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

settings = Settings()
