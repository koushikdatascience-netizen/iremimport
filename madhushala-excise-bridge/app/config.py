"""Configuration for Madhushala Excise Bridge."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Local settings object.

    Secrets are read from environment variables and must not be committed.
    """

    LOCAL_HOST: str = "127.0.0.1"
    LOCAL_PORT: int = 8091
    HEADLESS: bool = False
    EXCISE_LOGIN_URL: str = "https://excise.wb.gov.in/WBSBCL/Bevco/NIC/UserLogin/Login.aspx"
    BROWSER_PROFILE_DIR: str = "data/browser_profile"
    CAPTURES_DIR: str = "data/captures"
    MAPPINGS_DIR: str = "data/mappings"
    MADHUSHALA_BASE_URL: str = "https://reportapi.madhushalasoftware.com"
    MADHUSHALA_SHOP_CODE: str = "hedu_test3"
    MADHUSHALA_COMPANY_CODE: str = "2"
    MADHUSHALA_BILL_TYPE: str = "AI"
    CORS_ORIGINS: list[str] = field(
        default_factory=lambda: ["http://localhost:8091", "http://127.0.0.1:8091"]
    )

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
