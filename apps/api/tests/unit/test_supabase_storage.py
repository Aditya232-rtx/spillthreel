"""Storage URL contract regressions for signed object URLs."""

from types import SimpleNamespace

import pytest

from app.api.v1.items import ItemSummary
from app.services.media import supabase_storage


class _Response:
    status_code = 200

    def __init__(self, signed_url: str) -> None:
        self._signed_url = signed_url

    def json(self) -> dict[str, str]:
        return {"signedURL": self._signed_url}


class _Client:
    def __init__(self, response: _Response, calls: list[tuple[str, dict]]) -> None:
        self._response = response
        self._calls = calls

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, url: str, **kwargs) -> _Response:
        self._calls.append((url, kwargs))
        return self._response


@pytest.mark.asyncio
async def test_signed_storage_url_restores_storage_route_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        supabase_storage,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_url="https://project.supabase.co/",
            supabase_secret_key="test-service-key",
        ),
    )
    calls: list[tuple[str, dict]] = []
    response = _Response("/object/sign/media/user/item.jpg?token=signed-token")
    monkeypatch.setattr(
        supabase_storage.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(response, calls),
    )

    result = await supabase_storage.signed_url("media", "user/item.jpg")

    assert calls[0][0] == "https://project.supabase.co/storage/v1/object/sign/media/user/item.jpg"
    assert result == (
        "https://project.supabase.co/storage/v1/object/sign/media/user/item.jpg"
        "?token=signed-token"
    )


@pytest.mark.asyncio
async def test_signed_storage_url_accepts_response_with_prefix_already_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supabase_storage,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="test-service-key",
        ),
    )
    response = _Response(
        "/storage/v1/object/sign/media/user/item.jpg?token=signed-token"
    )
    monkeypatch.setattr(
        supabase_storage.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(response, []),
    )

    result = await supabase_storage.signed_url("media", "user/item.jpg")

    assert result == (
        "https://project.supabase.co/storage/v1/object/sign/media/user/item.jpg"
        "?token=signed-token"
    )


@pytest.mark.asyncio
async def test_signed_storage_url_rejects_unexpected_url_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supabase_storage,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="test-service-key",
        ),
    )
    response = _Response("https://other.example/object/sign/media/user/item.jpg?token=x")
    monkeypatch.setattr(
        supabase_storage.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(response, []),
    )

    with pytest.raises(supabase_storage.StorageError):
        await supabase_storage.signed_url("media", "user/item.jpg")


def test_legacy_signed_url_is_repaired_only_for_project_storage_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supabase_storage,
        "get_settings",
        lambda: SimpleNamespace(supabase_url="https://project.supabase.co"),
    )

    old_url = "https://project.supabase.co/object/sign/media/u/i.jpg?token=abc"
    assert supabase_storage.canonicalize_signed_url(old_url) == (
        "https://project.supabase.co/storage/v1/object/sign/media/u/i.jpg?token=abc"
    )
    assert supabase_storage.canonicalize_signed_url(
        "https://project.supabase.co/storage/v1/object/sign/media/u/i.jpg?token=abc"
    ) == "https://project.supabase.co/storage/v1/object/sign/media/u/i.jpg?token=abc"
    external = "https://images.example/i.jpg"
    assert supabase_storage.canonicalize_signed_url(external) == external
    foreign = "https://attacker.example/object/sign/media/u/i.jpg?token=abc"
    assert supabase_storage.canonicalize_signed_url(foreign) == foreign
    traversal = "https://project.supabase.co/object/sign/media/u/%2e%2e/admin?token=abc"
    assert supabase_storage.canonicalize_signed_url(traversal) == traversal


def test_item_response_serialization_repairs_legacy_signed_thumbnail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supabase_storage,
        "get_settings",
        lambda: SimpleNamespace(supabase_url="https://project.supabase.co"),
    )
    item = ItemSummary.model_validate(
        {
            "id": "item-id",
            "source_url": "https://instagram.com/p/example",
            "platform": "instagram",
            "state": "fully_indexed",
            "saved_at": "2026-10-21T00:00:00Z",
            "title": "Example",
            "summary": "Summary",
            "thumbnail_url": "https://project.supabase.co/object/sign/media/u/i.jpg?token=abc",
            "owner_username": None,
        }
    )
    assert item.thumbnail_url == (
        "https://project.supabase.co/storage/v1/object/sign/media/u/i.jpg?token=abc"
    )
