"""Database startup bootstrap.

PostgreSQL uses Alembic as the default schema manager. SQLite development can
still use SQLAlchemy create_all so the app remains quick to run locally.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import Base, DATABASE_URL, engine
from app import models  # noqa: F401 - ensure all models are registered

logger = logging.getLogger("life_os.database")


def _truthy_env(name: str, default: str = "true") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _app_env() -> str:
    return os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()


def _migrations_strict() -> bool:
    """Fail startup on migration errors in non-development environments by default."""
    default = "true" if _app_env() in {"production", "prod", "staging"} else "false"
    return _truthy_env("DB_MIGRATIONS_STRICT", default)


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "migrations"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _migration_mode() -> str:
    return os.getenv("DB_MIGRATION_MODE", "alembic").strip().lower()


def _migration_checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _ensure_migration_ledger() -> None:
    with engine.begin() as connection:
        connection.execute(text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version VARCHAR PRIMARY KEY,
              filename VARCHAR NOT NULL,
              checksum VARCHAR NOT NULL,
              applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        ))


def _migration_already_applied(connection, version: str, checksum: str) -> bool:
    row = connection.execute(
        text("SELECT checksum FROM schema_migrations WHERE version = :version"),
        {"version": version},
    ).fetchone()
    if row is None:
        return False

    stored_checksum = row[0]
    if stored_checksum != checksum:
        message = (
            f"database_migration_checksum_mismatch version={version} "
            f"stored={stored_checksum} current={checksum}"
        )
        if _migrations_strict():
            raise RuntimeError(message)
        logger.warning(message)
    return True


def _record_migration(connection, *, version: str, filename: str, checksum: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO schema_migrations (version, filename, checksum)
            VALUES (:version, :filename, :checksum)
            ON CONFLICT (version) DO UPDATE SET
              filename = EXCLUDED.filename,
              checksum = EXCLUDED.checksum
            """
        ),
        {"version": version, "filename": filename, "checksum": checksum},
    )


def _run_postgres_migrations() -> None:
    migrations_path = _migrations_dir()
    strict = _migrations_strict()
    if not migrations_path.exists():
        logger.info("database_migrations_skipped reason=no_migrations_dir path=%s", migrations_path)
        return

    migration_files = sorted(migrations_path.glob("*.sql"))
    if not migration_files:
        logger.info("database_migrations_skipped reason=no_sql_files")
        return

    _ensure_migration_ledger()

    for migration_file in migration_files:
        sql = migration_file.read_text(encoding="utf-8").strip()
        if not sql:
            continue
        version = migration_file.stem
        checksum = _migration_checksum(sql)
        try:
            with engine.begin() as connection:
                if _migration_already_applied(connection, version, checksum):
                    logger.info("database_migration_skipped file=%s reason=already_applied", migration_file.name)
                    continue
                connection.exec_driver_sql(sql)
                _record_migration(
                    connection,
                    version=version,
                    filename=migration_file.name,
                    checksum=checksum,
                )
            logger.info("database_migration_applied file=%s", migration_file.name)
        except Exception:
            logger.exception("database_migration_failed file=%s strict=%s", migration_file.name, strict)
            if strict:
                raise


def _run_alembic_upgrade() -> None:
    config_path = _project_root() / "alembic.ini"
    if not config_path.exists():
        raise RuntimeError(f"Alembic config not found: {config_path}")

    alembic_config = Config(str(config_path))
    alembic_config.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(alembic_config, "head")
    logger.info("database_alembic_upgrade_applied url=%s", DATABASE_URL)


def ensure_database_ready() -> None:
    """Create/repair the local database schema before the app serves requests."""
    if not _truthy_env("AUTO_DB_BOOTSTRAP", "true"):
        logger.info("database_bootstrap_skipped reason=env_disabled")
        return

    if DATABASE_URL.startswith("postgresql"):
        if _migration_mode() == "legacy_sql":
            try:
                Base.metadata.create_all(bind=engine)
                logger.info("database_tables_checked url=%s", DATABASE_URL)
            except SQLAlchemyError:
                logger.exception("database_create_all_failed")
                raise
            _run_postgres_migrations()
        else:
            _run_alembic_upgrade()
    else:
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("database_tables_checked url=%s", DATABASE_URL)
        except SQLAlchemyError:
            logger.exception("database_create_all_failed")
            raise
        logger.info("database_migrations_skipped reason=non_postgres url=%s", DATABASE_URL)
