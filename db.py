"""Storage for users and one-time login codes.

Uses DATABASE_URL if set (Render Postgres), otherwise a local SQLite file.
IMPORTANT: on Render's free plan the disk is wiped on every deploy, so SQLite
accounts disappear. Attach a Postgres database for accounts that persist.
"""

import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, DateTime, select,
    insert, update, delete,
)

DB_URL = os.environ.get("DATABASE_URL", "").strip()
if DB_URL.startswith("postgres://"):           # Render gives the old prefix
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)
if DB_URL.startswith("postgresql://"):
    # pick whichever postgres driver is actually installed
    try:
        import psycopg  # noqa: F401
        DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    except ImportError:
        DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
if not DB_URL:
    DB_URL = "sqlite:///lspso.db"

engine = create_engine(DB_URL, pool_pre_ping=True, future=True)
metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(320), unique=True, nullable=True),
    Column("phone", String(32), unique=True, nullable=True),
    Column("name", String(200)),
    Column("avatar", String(500)),
    Column("provider", String(20)),            # google | github | email
    Column("created_at", DateTime),
)

codes = Table(
    "otp_codes", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(320), nullable=False),
    Column("code_hash", String(64), nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("attempts", Integer, default=0),
    Column("sent_at", DateTime),
)


def init_db():
    metadata.create_all(engine)
    # add the phone column to databases created before phone sign-in existed
    try:
        with engine.begin() as c:
            cols = [r[1] for r in c.exec_driver_sql("PRAGMA table_info(users)")] \
                if engine.dialect.name == "sqlite" else []
            if engine.dialect.name == "sqlite" and cols and "phone" not in cols:
                c.exec_driver_sql("ALTER TABLE users ADD COLUMN phone VARCHAR(32)")
            elif engine.dialect.name == "postgresql":
                c.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(32)"
                )
    except Exception:
        pass


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- users

def get_user(user_id):
    with engine.begin() as c:
        row = c.execute(select(users).where(users.c.id == user_id)).mappings().first()
        return dict(row) if row else None


def upsert_user(email, name=None, avatar=None, provider="email"):
    email = email.strip().lower()
    with engine.begin() as c:
        row = c.execute(select(users).where(users.c.email == email)).mappings().first()
        if row:
            c.execute(
                update(users).where(users.c.id == row["id"]).values(
                    name=name or row["name"],
                    avatar=avatar or row["avatar"],
                )
            )
            return dict(row) | {"name": name or row["name"], "avatar": avatar or row["avatar"]}
        result = c.execute(
            insert(users).values(
                email=email, name=name, avatar=avatar,
                provider=provider, created_at=now(),
            )
        )
        return {
            "id": result.inserted_primary_key[0],
            "email": email, "name": name, "avatar": avatar, "provider": provider,
        }


def upsert_phone_user(phone, name=None):
    """Phone accounts have no email address."""
    phone = normalise_phone(phone)
    with engine.begin() as c:
        row = c.execute(select(users).where(users.c.phone == phone)).mappings().first()
        if row:
            return dict(row)
        result = c.execute(
            insert(users).values(
                phone=phone, name=name, provider="phone", created_at=now(),
            )
        )
        return {
            "id": result.inserted_primary_key[0],
            "email": None, "phone": phone, "name": name,
            "avatar": None, "provider": "phone",
        }


DEFAULT_COUNTRY_CODE = os.environ.get("DEFAULT_COUNTRY_CODE", "").strip().lstrip("+")


def normalise_phone(phone):
    """Returns the number in E.164 form (+countrycode + number), or "" if it
    can't be worked out. SMS providers reject anything else."""
    raw = (phone or "").strip()
    has_plus = raw.startswith("+") or raw.startswith("00")
    digits = "".join(ch for ch in raw if ch.isdigit())

    if raw.startswith("00"):
        digits = digits[2:]

    if not digits:
        return ""

    if has_plus:
        return "+" + digits.lstrip("0") if raw.startswith("00") else "+" + digits

    # a local number: prepend the configured country code, dropping any trunk 0
    if DEFAULT_COUNTRY_CODE:
        return "+" + DEFAULT_COUNTRY_CODE + digits.lstrip("0")

    return ""          # no country code and none configured -> reject


# ---------------------------------------------------------------- otp codes

def hash_code(identifier, code):
    salt = os.environ.get("SECRET_KEY", "lspso-dev")
    return hashlib.sha256(f"{salt}:{identifier.lower()}:{code}".encode()).hexdigest()


def create_code(email, ttl_minutes=10):
    """Returns (code, error). error is set if the user is asking too fast."""
    email = email.strip().lower()
    code = f"{secrets.randbelow(1000000):06d}"
    with engine.begin() as c:
        prev = c.execute(
            select(codes).where(codes.c.email == email).order_by(codes.c.id.desc())
        ).mappings().first()
        if prev and prev["sent_at"] and (now() - prev["sent_at"]).total_seconds() < 60:
            return None, "A code was just sent. Wait a minute before asking for another."
        c.execute(delete(codes).where(codes.c.email == email))
        c.execute(
            insert(codes).values(
                email=email,
                code_hash=hash_code(email, code),
                expires_at=now() + timedelta(minutes=ttl_minutes),
                attempts=0,
                sent_at=now(),
            )
        )
    return code, None


def verify_code(email, code):
    """Returns (ok, error)."""
    email = email.strip().lower()
    code = (code or "").strip()
    with engine.begin() as c:
        row = c.execute(
            select(codes).where(codes.c.email == email).order_by(codes.c.id.desc())
        ).mappings().first()

        if not row:
            return False, "No code was requested for that address."
        if row["expires_at"] < now():
            c.execute(delete(codes).where(codes.c.email == email))
            return False, "That code has expired. Request a new one."
        if row["attempts"] >= 5:
            c.execute(delete(codes).where(codes.c.email == email))
            return False, "Too many wrong attempts. Request a new code."
        if not secrets.compare_digest(row["code_hash"], hash_code(email, code)):
            c.execute(
                update(codes).where(codes.c.id == row["id"]).values(attempts=row["attempts"] + 1)
            )
            return False, "That code is not correct."

        c.execute(delete(codes).where(codes.c.email == email))
        return True, None
