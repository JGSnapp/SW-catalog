from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

try:
    from .models import AuthRequest, UserPublic, utc_now_iso
except ImportError:  # pragma: no cover
    from models import AuthRequest, UserPublic, utc_now_iso  # type: ignore


PBKDF2_ITERATIONS = 260_000


@dataclass(frozen=True)
class AuthSession:
    token: str
    user: UserPublic


def database_url() -> str:
    return os.getenv("DATABASE_URL") or "postgresql://stroky:stroky@postgres:5432/stroky"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)).hex()
    return hmac.compare_digest(digest, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or database_url()

    def _connect(self):
        return psycopg.connect(self.dsn)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_users (
                      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                      email text NOT NULL UNIQUE,
                      name text NOT NULL DEFAULT '',
                      password_hash text NOT NULL,
                      created_at timestamptz NOT NULL DEFAULT now(),
                      updated_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                      user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                      token_hash text NOT NULL UNIQUE,
                      created_at timestamptz NOT NULL DEFAULT now(),
                      expires_at timestamptz NOT NULL,
                      revoked_at timestamptz
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON auth_sessions(user_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_hash ON auth_sessions(token_hash)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_storage_documents (
                      user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                      key text NOT NULL,
                      payload jsonb NOT NULL,
                      updated_at timestamptz NOT NULL DEFAULT now(),
                      PRIMARY KEY (user_id, key)
                    )
                    """
                )

    def register(self, payload: AuthRequest) -> AuthSession:
        email = _normalize_email(payload.email)
        if not email or "@" not in email:
            raise ValueError("Некорректный email.")
        password_hash = _hash_password(payload.password)
        with self._connect() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO app_users (email, name, password_hash)
                        VALUES (%s, %s, %s)
                        RETURNING id::text, email, name, created_at
                        """,
                        (email, payload.name.strip(), password_hash),
                    )
                except psycopg.errors.UniqueViolation as exc:
                    raise ValueError("Пользователь с таким email уже существует.") from exc
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("Не удалось создать пользователя.")
                user = UserPublic(id=row[0], email=row[1], name=row[2], created_at=self._iso(row[3]))
        return self._create_session(user)

    def login(self, payload: AuthRequest) -> AuthSession:
        email = _normalize_email(payload.email)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id::text, email, name, created_at, password_hash FROM app_users WHERE email = %s",
                    (email,),
                )
                row = cur.fetchone()
        if row is None or not _verify_password(payload.password, row[4]):
            raise ValueError("Неверный email или пароль.")
        return self._create_session(UserPublic(id=row[0], email=row[1], name=row[2], created_at=self._iso(row[3])))

    def _create_session(self, user: UserPublic) -> AuthSession:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=30)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO auth_sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                    (user.id, _token_hash(token), expires_at),
                )
        return AuthSession(token=token, user=user)

    def user_for_token(self, token: str) -> UserPublic | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.id::text, u.email, u.name, u.created_at
                    FROM auth_sessions s
                    JOIN app_users u ON u.id = s.user_id
                    WHERE s.token_hash = %s
                      AND s.revoked_at IS NULL
                      AND s.expires_at > now()
                    """,
                    (_token_hash(token),),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return UserPublic(id=row[0], email=row[1], name=row[2], created_at=self._iso(row[3]))

    def revoke(self, token: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth_sessions SET revoked_at = now() WHERE token_hash = %s",
                    (_token_hash(token),),
                )

    def _iso(self, value: datetime) -> str:
        if value.tzinfo is not None:
            value = value.astimezone(tz=None).replace(tzinfo=None)
        return value.replace(microsecond=0).isoformat() + "Z"
