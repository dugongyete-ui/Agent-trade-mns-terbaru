import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).parent
backend_path = project_root / "backend"
sys.path.insert(0, str(backend_path))

env_path = backend_path / ".env"
if env_path.exists():
    load_dotenv(str(env_path))

async def test_mongodb():
    print("\n--- Mengetes MongoDB ---")
    from motor.motor_asyncio import AsyncIOMotorClient
    uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("MONGODB_DATABASE", "dzeck")
    
    if not uri:
        print("MONGODB_URI tidak dikonfigurasi — skip")
        return None
    
    try:
        client = AsyncIOMotorClient(uri)
        db = client[db_name]
        await db.command("ping")
        print(f"Berhasil terhubung ke MongoDB: {db_name}")
        return True
    except Exception as e:
        print(f"Gagal terhubung ke MongoDB: {e}")
        return False

async def test_redis():
    print("\n--- Mengetes Redis ---")
    import redis.asyncio as redis
    host = os.getenv("REDIS_HOST")
    port_str = os.getenv("REDIS_PORT", "6379")
    password = os.getenv("REDIS_PASSWORD")
    
    if not host:
        print("REDIS_HOST tidak dikonfigurasi — skip")
        return None
    
    try:
        r = redis.Redis(host=host, port=int(port_str), password=password, decode_responses=True)
        await r.ping()
        print(f"Berhasil terhubung ke Redis: {host}:{port_str}")
        return True
    except Exception as e:
        print(f"Gagal terhubung ke Redis: {e}")
        return False

async def main():
    mongo_ok = await test_mongodb()
    redis_ok = await test_redis()
    
    print("\n--- Ringkasan Hasil ---")
    print(f"MongoDB: {'OK' if mongo_ok else 'FAILED' if mongo_ok is False else 'SKIP'}")
    print(f"Redis: {'OK' if redis_ok else 'FAILED' if redis_ok is False else 'SKIP'}")

if __name__ == "__main__":
    asyncio.run(main())
