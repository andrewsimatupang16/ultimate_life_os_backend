# Database Setup with uv + Alembic

Backend ini memakai PostgreSQL dengan Alembic sebagai migration system utama.

## 1. Install Dependency Backend

```bash
cd audit_app
uv sync
```

## 2. Siapkan Env

```bash
cp .env.example .env
```

Isi PostgreSQL:

```env
APP_ENV=development
JWT_SECRET_KEY=dev-local-change-me

DB_USER=life_os
DB_PASSWORD=life_os_password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=life_os

DB_MIGRATION_MODE=alembic
AUTO_DB_BOOTSTRAP=true
RATE_LIMIT_ENABLED=false
```

## 3. Jalankan Migration

```bash
uv run alembic upgrade head
```

Alternatif:

```bash
uv run python init_db.py
```

`init_db.py` akan memanggil Alembic jika database yang dipakai adalah PostgreSQL.

## 4. Jalankan Backend

```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 5. Kalau Model Berubah

Setiap perubahan schema harus dibuat migration baru:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

Selalu cek file migration hasil autogenerate sebelum dijalankan.

## 6. Reset PostgreSQL Development

Gunakan hanya untuk development karena semua data akan hilang.

```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```

Lalu jalankan:

```bash
uv run alembic upgrade head
```

## 7. Route Database Lama

File SQL lama di folder `migrations/*.sql` masih disimpan sebagai arsip kompatibilitas. Jalur default sekarang adalah Alembic di `migrations/versions`.
