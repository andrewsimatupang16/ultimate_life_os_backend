"""
Script untuk inisialisasi dan sinkronisasi database lokal.
Jalankan:
    uv run python init_db.py

PostgreSQL:
    Default memakai Alembic upgrade head.

SQLite development:
    Default memakai SQLAlchemy create_all.
"""

from app.database import Base, DATABASE_URL
from app.services.database_bootstrap import ensure_database_ready
from app import models  # noqa: F401 - wajib di-import agar semua model terdaftar di Base.metadata


def main():
    print("Database URL:", DATABASE_URL)
    print("Checking tables and applying bundled migrations when supported...")

    ensure_database_ready()

    print("Database schema checked successfully!")
    print("")
    print("Known SQLAlchemy tables:")
    for table_name in sorted(Base.metadata.tables.keys()):
        print(f"  - {table_name}")


if __name__ == "__main__":
    main()
