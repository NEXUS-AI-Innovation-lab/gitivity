from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.midpoint_client import MidPointClient


def response(payload=None, status_code=200):
    value = MagicMock()
    value.status_code = status_code
    value.json.return_value = payload or {}
    return value


@pytest.mark.asyncio
async def test_remove_role_deletes_assignment_by_container_id_in_raw_mode():
    role_oid = "role-to-remove"
    client = AsyncMock()

    async def get(url):
        if url == f"/ws/rest/roles/{role_oid}":
            return response()
        if url == "/ws/rest/users":
            return response({"object": {"list": [{"oid": "user-1"}]}})
        if url == "/ws/rest/users/user-1":
            return response(
                {
                    "user": {
                        "assignment": {
                            "@id": 15,
                            "targetRef": {"oid": role_oid},
                            "activation": {"effectiveStatus": "enabled"},
                            "metadata": {"storage": {"createTimestamp": "ignored"}},
                        }
                    }
                }
            )
        return response({"object": {"list": []}})

    client.get.side_effect = get
    client.patch.return_value = response()
    client.delete.return_value = response(status_code=204)
    midpoint = MidPointClient()
    midpoint._get_client = AsyncMock(return_value=client)

    result = await midpoint.remove_role_everywhere(role_oid)

    assert client.patch.await_args.kwargs == {
        "json": {
            "objectModification": {
                "itemDelta": [
                    {
                        "modificationType": "delete",
                        "path": "assignment",
                        "value": [{"@id": 15}],
                    }
                ]
            }
        },
        "params": {"options": "raw"},
    }
    client.delete.assert_awaited_once_with(
        f"/ws/rest/roles/{role_oid}", params={"options": "raw"}
    )
    assert result["references_removed"] == 1
    assert result["affected_users"] == [
        {
            "oid": "user-1",
            "username": "",
            "email": None,
            "remaining_role_oids": [],
        }
    ]


@pytest.mark.asyncio
async def test_get_resources_accepts_midpoint_object_object_collection():
    client = AsyncMock()
    client.get.return_value = response({
        "object": {
            "object": {
                "oid": "gateway-resource-oid",
                "name": "gateway-iam",
            }
        }
    })
    midpoint = MidPointClient()
    midpoint._get_client = AsyncMock(return_value=client)

    resources = await midpoint.get_resources()

    assert resources == [{
        "oid": "gateway-resource-oid",
        "name": "gateway-iam",
    }]


@pytest.mark.asyncio
async def test_remove_user_projection_unlinks_before_raw_shadow_deletion():
    client = AsyncMock()

    async def get(url, **_kwargs):
        if url == "/ws/rest/users/user-oid":
            return response({
                "user": {
                    "linkRef": [
                        {"oid": "gateway-shadow", "type": "c:ShadowType"},
                        {"oid": "other-shadow", "type": "c:ShadowType"},
                    ]
                }
            })
        resource_oid = (
            "gateway-resource" if url.endswith("gateway-shadow") else "other-resource"
        )
        return response({"shadow": {"resourceRef": {"oid": resource_oid}}})

    client.get.side_effect = get
    client.patch.return_value = response(status_code=204)
    client.delete.return_value = response(status_code=204)
    midpoint = MidPointClient()
    midpoint._get_client = AsyncMock(return_value=client)

    result = await midpoint.remove_user_resource_projection(
        "user-oid", "gateway-resource"
    )

    assert result == {
        "projection_deleted": True,
        "deleted_shadow_oids": ["gateway-shadow"],
    }
    assert client.patch.await_args.args == ("/ws/rest/users/user-oid",)
    assert client.patch.await_args.kwargs == {
        "json": {
            "objectModification": {
                "itemDelta": [{
                    "modificationType": "delete",
                    "path": "linkRef",
                    "value": [{
                        "oid": "gateway-shadow",
                        "type": "c:ShadowType",
                    }],
                }]
            }
        },
        "params": {"options": "raw"},
    }
    client.delete.assert_awaited_once_with(
        "/ws/rest/shadows/gateway-shadow", params={"options": "raw"}
    )


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
