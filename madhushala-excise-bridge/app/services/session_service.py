import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from fastapi import HTTPException, Request
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.db import conn


CANONICAL_PUBLIC_BASE_URL = "https://integrations.madhushalasoftware.com/excise-import"


def _safe_public_base_url(raw_url: str) -> str:
    parsed = urlparse((raw_url or "").strip())
    hostname = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")

    if hostname == "integrations.madhushalasoftware.com":
        if path.endswith("/excise-import"):
            return f"{parsed.scheme or 'https'}://{hostname}{path}"
        return CANONICAL_PUBLIC_BASE_URL
    if hostname in {"13.232.52.191", "excise.connect.snapkey.in"}:
        return CANONICAL_PUBLIC_BASE_URL
    if parsed.scheme == "http" and hostname not in {"127.0.0.1", "localhost"}:
        return CANONICAL_PUBLIC_BASE_URL
    return (raw_url or CANONICAL_PUBLIC_BASE_URL).rstrip("/")


class SessionService:
    def _cipher(self):
        if not settings.SESSION_ENCRYPTION_KEY:
            return None
        try:
            return Fernet(settings.SESSION_ENCRYPTION_KEY.encode())
        except ValueError as exc:
            raise RuntimeError("SESSION_ENCRYPTION_KEY must be a valid Fernet key") from exc

    def _encrypt_token(self, token: str) -> str:
        cipher = self._cipher()
        if not cipher or not token:
            return token
        return "enc:" + cipher.encrypt(token.encode()).decode()

    def _decrypt_token(self, token: str) -> str:
        if not token or not token.startswith("enc:"):
            return token
        cipher = self._cipher()
        if not cipher:
            raise HTTPException(500, "Session encryption key is not configured")
        try:
            return cipher.decrypt(token[4:].encode()).decode()
        except InvalidToken as exc:
            raise HTTPException(401, "Invalid encrypted integration session") from exc

    def create(self, shop_code, company_code, bill_type, madhushala_token):
        sid = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        stored_madhushala_token = self._encrypt_token(madhushala_token)
        now = datetime.now(timezone.utc)
        exp = now + timedelta(minutes=settings.SESSION_TTL_MINUTES)
        with conn() as db:
            db.execute("INSERT INTO integration_sessions(session_id,session_token,shop_code,company_code,bill_type,madhushala_token,created_at,expires_at,state) VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, token, shop_code, company_code, bill_type, stored_madhushala_token, now.isoformat(), exp.isoformat(), "created"))
        base = _safe_public_base_url(settings.APP_BASE_URL)
        return {
            "sessionId": sid,
            "sessionToken": token,
            "expiresAt": exp.isoformat(),
            "launchUrl": f"{base}/?sessionId={quote(sid)}#session={quote(token)}",
            "documentImportUrl": f"{base}/document-import?sessionId={quote(sid)}#session={quote(token)}",
            "mappingUrl": f"{base}/?view=mapping&sessionId={quote(sid)}#session={quote(token)}",
        }

    def by_token(self, token):
        with conn() as db:
            row = db.execute("SELECT * FROM integration_sessions WHERE session_token=?", (token,)).fetchone()
        if not row:
            raise HTTPException(401, "Invalid integration session")
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            raise HTTPException(401, "Integration session expired")
        session = dict(row)
        session["madhushala_token"] = self._decrypt_token(session.get("madhushala_token", ""))
        return session

    def from_request(self, request: Request):
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(401, "Missing integration session")
        return self.by_token(auth[7:].strip())

    def cleanup_expired(self):
        now = datetime.now(timezone.utc).isoformat()
        with conn() as db:
            db.execute("DELETE FROM integration_sessions WHERE expires_at < ?", (now,))


session_service = SessionService()
