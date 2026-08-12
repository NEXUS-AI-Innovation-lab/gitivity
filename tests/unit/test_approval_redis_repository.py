import json

import pytest

from app.db.repositories.approval_redis_repository import ApprovalRedisRepository


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self.store[key] = value
        self.ttls[key] = ttl
        return True

    async def set(self, key: str, value: str) -> bool:
        self.store[key] = value
        self.ttls.pop(key, None)
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        existed = key in self.store
        self.store.pop(key, None)
        self.ttls.pop(key, None)
        return 1 if existed else 0

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)


@pytest.mark.asyncio
async def test_store_user_state_uses_canonical_target_id():
    repo = ApprovalRedisRepository(FakeRedis())

    await repo.store_user_state("Mario", "MONGODB", {"username": "Mario"})

    assert "user_state:mongodb:Mario" in repo.redis.store
    assert "user_state:MONGODB:Mario" not in repo.redis.store


@pytest.mark.asyncio
async def test_get_user_state_migrates_legacy_uppercase_key():
    redis = FakeRedis()
    redis.store["user_state:MONGODB:Mario"] = json.dumps({"username": "Mario"})
    redis.ttls["user_state:MONGODB:Mario"] = 3600
    repo = ApprovalRedisRepository(redis)

    state = await repo.get_user_state("Mario", "mongodb")

    assert state == {"username": "Mario"}
    assert "user_state:mongodb:Mario" in redis.store
    assert "user_state:MONGODB:Mario" not in redis.store
    assert redis.ttls["user_state:mongodb:Mario"] == 3600


@pytest.mark.asyncio
async def test_delete_user_state_clears_canonical_and_legacy_keys():
    redis = FakeRedis()
    redis.store["user_state:mongodb:Mario"] = "{}"
    redis.store["user_state:MONGODB:Mario"] = "{}"
    repo = ApprovalRedisRepository(redis)

    assert await repo.delete_user_state("Mario", "mongodb") is True
    assert not any(key.endswith(":Mario") for key in redis.store)


@pytest.mark.asyncio
async def test_rejected_create_checks_and_clears_legacy_service_key():
    redis = FakeRedis()
    redis.store["rejected_create:MONGODB:Mario"] = json.dumps({"operation_id": "op-1"})
    redis.ttls["rejected_create:MONGODB:Mario"] = 3600
    repo = ApprovalRedisRepository(redis)

    assert await repo.check_rejected_create("Mario", "mongodb") is True
    assert "rejected_create:mongodb:Mario" in redis.store
    assert "rejected_create:MONGODB:Mario" not in redis.store

    assert await repo.clear_rejected_create("Mario", "mongo") is True
    assert "rejected_create:mongodb:Mario" not in redis.store


@pytest.mark.asyncio
async def test_deleted_user_marker_is_durable_until_cleared():
    repo = ApprovalRedisRepository(FakeRedis())

    assert await repo.mark_user_deleted("Mario", "MONGODB", "delete-op")
    assert await repo.was_user_deleted("Mario", "mongodb")
    assert "deleted_user:mongodb:Mario" in repo.redis.store

    assert await repo.clear_user_deleted("Mario", "mongo")
    assert not await repo.was_user_deleted("Mario", "mongodb")
