"""A deletion tombstone prevents profile recreation by a still-valid JWT."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.auth.middleware import _current_user


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _Session:
    def __init__(self, profile: Any = None, tombstone: Any = None) -> None:
        self.profile = profile
        self.tombstone = tombstone
        self.calls = 0
        self.add = Mock()

    async def execute(self, _statement: Any, *_args: Any) -> _Result:
        self.calls += 1
        if self.calls == 1:
            return _Result(None)  # transaction-local JWT claim
        if self.calls == 2:
            return _Result(self.profile)
        return _Result(self.tombstone)


@pytest.mark.asyncio
async def test_deleted_account_jwt_cannot_recreate_missing_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.auth.middleware as middleware

    user_id = "123e4567-e89b-12d3-a456-426614174000"
    monkeypatch.setattr(middleware, "verify_access_token", lambda _token: {"sub": user_id})
    session = _Session(profile=None, tombstone=user_id)

    with pytest.raises(HTTPException) as error:
        await _current_user("Bearer still-valid-token", session)  # type: ignore[arg-type]

    assert error.value.status_code == 403
    assert error.value.detail == {
        "code": "account_deleted",
        "message": "account deleted",
    }
    assert session.calls == 3
    session.add.assert_not_called()
