"""
Email + password authentication using only the standard library.

Passwords are hashed with scrypt and a per-user random salt. Logins create a
random session token that is sent to the browser as an HttpOnly cookie; only
a SHA-256 hash of the token is stored, so a leaked database can't be used to
hijack sessions.
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response

import db

SESSION_COOKIE = "resume_desk_session"
SESSION_DAYS = 14
# Set COOKIE_SECURE=1 when serving over HTTPS so the cookie is never sent in clear text.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 64}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


# A fixed hash used to keep login timing the same whether or not the email exists.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    db.create_session(_token_hash(token), user_id, expires.isoformat(timespec="seconds"))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def end_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(_token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")


def authenticate(email: str, password: str):
    """Return the user row if the credentials are valid, else None."""
    user = db.get_user_by_email(email)
    if not user:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user["password_hash"]) else None


def current_user(request: Request):
    """FastAPI dependency: the logged-in user, or a 401."""
    token = request.cookies.get(SESSION_COOKIE)
    user = db.get_session_user(_token_hash(token)) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user
