import asyncio
import asyncpg
import os
from app.security.auth import get_password_hash
from app.config import load_runtime_config


async def run_sql_file(conn, file_path):
    if not os.path.exists(file_path):
        print(f"Warning: File {file_path} not found.")
        return
    with open(file_path, "r", encoding="utf-8") as f:
        sql = f.read()
    print(f"Executing {file_path}...")
    # asyncpg execute can run multiple statements separated by semicolons
    await conn.execute(sql)


async def seed_user():
    config = load_runtime_config()
    db_url = config.session_db_uri

    # Strip +asyncpg from the scheme if present, as asyncpg.connect expects standard postgresql://
    if "postgresql+asyncpg://" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    print(f"Connecting to database: {db_url}")
    conn = await asyncpg.connect(db_url)
    try:
        # 1. Initialize Schema
        await run_sql_file(conn, "db/schema.sql")
        await run_sql_file(conn, "db/audit_schema.sql")

        # 2. Seed Data
        await run_sql_file(conn, "db/seed.sql")

        # 3. Create Test User
        username = "testuser"
        password = "password123"
        hashed_password = get_password_hash(password)

        await conn.execute(
            "INSERT INTO users (username, hashed_password) VALUES ($1, $2) ON CONFLICT (username) DO NOTHING",
            username,
            hashed_password,
        )
        print(f"User '{username}' checked/created successfully.")

        print("Database initialization and seeding completed successfully.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed_user())
