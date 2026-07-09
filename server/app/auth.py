"""Single-user login with a signed session cookie."""
import bcrypt
from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

SESSION_COOKIE = "o2h_session"


class LoginRequired(Exception):
    """Raised by require_login; converted to a redirect by an exception handler."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="o2h-session")


def create_session_token(username: str) -> str:
    return _serializer().dumps({"u": username})


def get_session_user(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("u")


def verify_credentials(username: str, password: str) -> bool:
    if username != settings.web_user or not settings.web_password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), settings.web_password_hash.encode("utf-8")
        )
    except ValueError:
        return False


def require_login(request: Request) -> str:
    user = get_session_user(request)
    if not user:
        raise LoginRequired()
    return user
