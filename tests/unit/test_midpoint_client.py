from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.midpoint_client import MidPointClient


def response(payload=None, status_code=200):
    value = MagicMock()
    value.status_code = status_code
    value.json.return_value = payload or {}
    return value


@pytest.mark.asyncio
async def test_replace_role_uses_atomic_container_replacement():
    old_oid = "old-role"
    new_oid = "new-role"
    user_oid = "user-1"
    client = AsyncMock()

    async def get(url):
        if url in {f"/ws/rest/roles/{old_oid}", f"/ws/rest/roles/{new_oid}"}:
            return response()
        if url == "/ws/rest/users":
            return response({"object": {"list": [{"oid": user_oid}]}})
        if url == f"/ws/rest/users/{user_oid}":
            return response(
                {
                    "user": {
                        "assignment": [
                            {
                                "@id": 12,
                                "targetRef": {
                                    "oid": old_oid,
                                    "targetName": "Legacy role",
                                },
                            },
                            {"@id": 13, "targetRef": {"oid": "unrelated-role"}},
                        ]
                    }
                }
            )
        return response({"object": {"list": []}})

    client.get.side_effect = get
    client.patch.return_value = response()
    midpoint = MidPointClient()
    midpoint._get_client = AsyncMock(return_value=client)

    result = await midpoint.replace_role_everywhere(old_oid, new_oid)

    assert client.patch.await_args_list[0].args == (f"/ws/rest/users/{user_oid}",)
    assert client.patch.await_args_list[0].kwargs["json"] == {
        "objectModification": {
            "itemDelta": [
                {
                    "modificationType": "add",
                    "path": "assignment",
                    "value": [{"targetRef": {"oid": new_oid}}],
                }
            ]
        }
    }
    assert client.patch.await_args_list[1].kwargs["json"] == {
        "objectModification": {
            "itemDelta": [
                {
                    "modificationType": "delete",
                    "path": "assignment",
                    "value": [{"@id": 12}],
                }
            ]
        }
    }
    assert result == {
        "old_role_absent": False,
        "references_replaced": 1,
        "objects_modified": 1,
    }


@pytest.mark.asyncio
async def test_replace_role_drops_legacy_duplicate_of_existing_replacement():
    old_oid = "old-role"
    new_oid = "new-role"
    client = AsyncMock()

    async def get(url):
        if url in {f"/ws/rest/roles/{old_oid}", f"/ws/rest/roles/{new_oid}"}:
            return response()
        if url == "/ws/rest/users":
            return response({"object": {"list": [{"oid": "user-1"}]}})
        if url == "/ws/rest/users/user-1":
            return response(
                {
                    "user": {
                        "assignment": [
                            {"@id": 12, "targetRef": {"oid": old_oid}},
                            {"@id": 13, "targetRef": {"oid": new_oid}},
                        ]
                    }
                }
            )
        return response({"object": {"list": []}})

    client.get.side_effect = get
    client.patch.return_value = response()
    midpoint = MidPointClient()
    midpoint._get_client = AsyncMock(return_value=client)

    await midpoint.replace_role_everywhere(old_oid, new_oid)

    client.patch.assert_awaited_once()
    patch_body = client.patch.await_args.kwargs["json"]
    assert patch_body["objectModification"]["itemDelta"][0] == {
        "modificationType": "delete",
        "path": "assignment",
        "value": [{"@id": 12}],
    }
