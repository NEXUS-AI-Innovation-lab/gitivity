"""Integration tests against the Docker MongoDB target.

Run with:
    RUN_MONGODB_INTEGRATION=1 pytest tests/integration/test_mongodb_connector.py
"""

import os
import uuid

import pytest
from pymongo import MongoClient

from app.core.connectors.mongodb_connector import MongoDBConnector


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MONGODB_INTEGRATION") != "1",
    reason="set RUN_MONGODB_INTEGRATION=1 and start mongodb-target",
)


@pytest.mark.asyncio
async def test_mongodb_user_lifecycle(monkeypatch):
    monkeypatch.setattr("app.config.settings.settings.MONGODB_HOST", os.getenv("MONGODB_HOST", "localhost"))
    monkeypatch.setattr("app.config.settings.settings.MONGODB_PORT", int(os.getenv("MONGODB_PORT", "27017")))
    monkeypatch.setattr("app.config.settings.settings.MONGODB_USER", os.getenv("MONGODB_USER", "root"))
    monkeypatch.setattr("app.config.settings.settings.MONGODB_PASSWORD", os.getenv("MONGODB_PASSWORD", "mongodb_root_secret"))
    monkeypatch.setattr("app.config.settings.settings.MONGODB_DATABASE", "target_db")
    monkeypatch.setattr("app.config.settings.settings.MONGODB_AUTH_SOURCE", "admin")

    username = f"pytest_{uuid.uuid4().hex[:10]}"
    connector = MongoDBConnector()
    await connector.connect()
    try:
        created = await connector.provision_user(
            username,
            "TestMongoP@ss123!",
            "mongodb-test@example.com",
            ["mongodb"],
            {"mongodbRoles": ["read@target_db"]},
        )
        assert created.success
        assert await connector._user_exists(username, "target_db")
        created_info = connector._client["target_db"].command(
            {"usersInfo": {"user": username, "db": "target_db"}}
        )["users"][0]
        assert created_info["roles"] == [{"role": "read", "db": "target_db"}]

        native_user_client = MongoClient(
            host=os.getenv("MONGODB_HOST", "localhost"),
            port=int(os.getenv("MONGODB_PORT", "27017")),
            username=username,
            password="TestMongoP@ss123!",
            authSource="target_db",
            serverSelectionTimeoutMS=5000,
        )
        try:
            assert native_user_client.admin.command("ping")["ok"] == 1
        finally:
            native_user_client.close()

        updated = await connector.update_user(
            username,
            roles=["mongodb"],
            attributes={"mongodbRoles": ["readWrite@target_db"]},
        )
        assert updated.success
        updated_info = connector._client["target_db"].command(
            {"usersInfo": {"user": username, "db": "target_db"}}
        )["users"][0]
        assert updated_info["roles"] == [{"role": "readWrite", "db": "target_db"}]

        deleted = await connector.delete_user(username)
        assert deleted.success
        assert not await connector._user_exists(username, "target_db")
    finally:
        if await connector._user_exists(username, "target_db"):
            await connector.delete_user(username)
        await connector.disconnect()
