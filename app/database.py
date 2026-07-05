import os
import sys
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()


def build_database_url() -> str:
    """
    Prioritas konfigurasi database:
    1. DATABASE_URL jika ada di .env
    2. DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME untuk PostgreSQL
    3. Fallback SQLite lokal agar backend tetap bisa start untuk development
    """
    app_env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).lower()
    if any("pytest" in arg for arg in sys.argv):
        return os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT")
    db_name = os.getenv("DB_NAME")

    if all([db_user, db_password, db_host, db_port, db_name]):
        safe_user = quote_plus(db_user)
        safe_password = quote_plus(db_password)
        return f"postgresql+psycopg2://{safe_user}:{safe_password}@{db_host}:{db_port}/{db_name}"

    if app_env in {"production", "prod", "staging"}:
        raise RuntimeError("DATABASE_URL or DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME must be set outside development")

    return "sqlite:///./life_os.db"


DATABASE_URL = build_database_url()

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
