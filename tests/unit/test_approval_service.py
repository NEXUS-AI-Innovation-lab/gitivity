"""Unit tests for the multi-level approval chain service"""
from unittest.mock import AsyncMock

import pytest

from app.services.approval_service import ApprovalService, WORKER_ID


def make_service():
    """ApprovalService with an in-memory fake Redis repo and a mocked email sender."""
    chains: dict[str, dict] = {}
    tokens: dict[str, dict] = {}

    repo = AsyncMock()

    async def store_chain(operation_id, chain_data):
        # JSON round-trip semantics: store a copy
        chains[operation_id] = dict(chain_data)
        return True

    async def get_chain(operation_id):
        data = chains.get(operation_id)
        return dict(data) if data else None

    async def delete_chain(operation_id):
        return chains.pop(operation_id, None) is not None

    async def store_decision_token(token, token_data):
        tokens[token] = dict(token_data)
        return True

    async def consume_decision_token(token):
        return tokens.pop(token, None)

    async def delete_decision_token(token):
        return tokens.pop(token, None) is not None

    repo.store_chain.side_effect = store_chain
    repo.get_chain.side_effect = get_chain
    repo.delete_chain.side_effect = delete_chain
    repo.store_decision_token.side_effect = store_decision_token
    repo.consume_decision_token.side_effect = consume_decision_token
    repo.delete_decision_token.side_effect = delete_decision_token

    email = AsyncMock()
    service = ApprovalService(repo, email)
    return service, repo, email, chains, tokens


USER_DATA = {
    "username": "jdoe",
    "email": "jdoe@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "password": "s3cret",
    "roles": ["mysql-readonly"],
    "attributes": {"description": "Dev"},
}

APPROVERS = [
    {"email": "lvl2@example.com", "name": "Directeur", "level": 2},
    {"email": "lvl1@example.com", "name": "Manager", "level": 1},
]


async def start(service, approvers=None):
    await service.start_chain(
        operation_id="op-1",
        request_id="req-1",
        operation_type="CREATE_USER",
        target_service="MYSQL",
        user_data=USER_DATA,
        approvers=APPROVERS if approvers is None else approvers,
        admin_email="admin@example.com",
    )


@pytest.mark.asyncio
async def test_start_chain_emails_level_1_first():
    service, _, email, chains, tokens = make_service()
    await start(service)

    # Email sent to the level-1 approver despite input being unsorted
    email.send_html.assert_awaited_once()
    assert email.send_html.await_args.args[0] == "lvl1@example.com"
    assert chains["op-1"]["current_index"] == 0
    assert len(tokens) == 1


@pytest.mark.asyncio
async def test_fallback_to_admin_email_when_no_approvers():
    service, _, email, chains, _ = make_service()
    await start(service, approvers=[])

    assert email.send_html.await_args.args[0] == "admin@example.com"
    assert chains["op-1"]["total"] == 1


@pytest.mark.asyncio
async def test_intermediate_approval_advances_to_next_level():
    service, _, email, chains, tokens = make_service()
    await start(service)
    token = next(iter(tokens))

    orchestrator = AsyncMock()
    result = await service.handle_decision(token, approved=True, orchestrator=orchestrator)

    assert result["status"] == "next_level"
    orchestrator.process_approval_response.assert_not_awaited()
    # Second email to level-2 approver
    assert email.send_html.await_count == 2
    assert email.send_html.await_args.args[0] == "lvl2@example.com"
    assert chains["op-1"]["current_index"] == 1


@pytest.mark.asyncio
async def test_final_approval_provisions_and_sends_confirmation():
    service, _, email, chains, tokens = make_service()
    await start(service)

    orchestrator = AsyncMock()
    # Level 1 approves
    token1 = next(iter(tokens))
    await service.handle_decision(token1, approved=True, orchestrator=orchestrator)
    # Level 2 approves
    token2 = next(iter(tokens))
    result = await service.handle_decision(token2, approved=True, orchestrator=orchestrator)

    assert result["status"] == "approved"
    orchestrator.process_approval_response.assert_awaited_once_with(
        operation_id="op-1",
        approved=True,
        reason="Approved by all approvers",
        worker_id=WORKER_ID,
    )
    # 2 approval emails + 1 confirmation email to the user
    assert email.send_html.await_count == 3
    assert email.send_html.await_args.args[0] == "jdoe@example.com"
    # Chain cleaned up
    assert "op-1" not in chains
    assert tokens == {}


@pytest.mark.asyncio
async def test_rejection_short_circuits_chain():
    service, _, email, chains, tokens = make_service()
    await start(service)
    token = next(iter(tokens))

    orchestrator = AsyncMock()
    result = await service.handle_decision(token, approved=False, orchestrator=orchestrator)

    assert result["status"] == "rejected"
    orchestrator.process_approval_response.assert_awaited_once_with(
        operation_id="op-1",
        approved=False,
        reason="Rejected by Manager (Level 1)",
        worker_id=WORKER_ID,
    )
    # No further approval email, no confirmation email
    assert email.send_html.await_count == 1
    assert "op-1" not in chains


@pytest.mark.asyncio
async def test_token_is_single_use():
    service, _, _, _, tokens = make_service()
    await start(service)
    token = next(iter(tokens))

    orchestrator = AsyncMock()
    first = await service.handle_decision(token, approved=False, orchestrator=orchestrator)
    second = await service.handle_decision(token, approved=False, orchestrator=orchestrator)

    assert first["status"] == "rejected"
    assert second["status"] == "invalid"
    orchestrator.process_approval_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_token_is_invalid():
    service, _, _, _, _ = make_service()
    orchestrator = AsyncMock()
    result = await service.handle_decision("garbage", approved=True, orchestrator=orchestrator)
    assert result["status"] == "invalid"
    orchestrator.process_approval_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_email_failure_does_not_raise():
    service, _, email, _, tokens = make_service()
    await start(service, approvers=[{"email": "a@b.c", "name": "Solo", "level": 1}])
    token = next(iter(tokens))

    # First call (approval email at start) already happened.
    # Make the confirmation send fail.
    email.send_html.side_effect = [RuntimeError("smtp down")]

    orchestrator = AsyncMock()
    result = await service.handle_decision(token, approved=True, orchestrator=orchestrator)
    assert result["status"] == "approved"
