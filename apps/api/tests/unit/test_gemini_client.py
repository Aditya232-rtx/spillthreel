import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.llm.client import create_embedding_client, create_genai_client, generation_temperature


def _install_fake_genai(monkeypatch: pytest.MonkeyPatch, constructor: Mock) -> None:
    google_module = ModuleType("google")
    genai_module = ModuleType("google.genai")
    genai_module.Client = constructor  # type: ignore[attr-defined]
    google_module.genai = genai_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)


def test_developer_api_client_uses_configured_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = object()
    constructor = Mock(return_value=client)
    _install_fake_genai(monkeypatch, constructor)

    result = create_genai_client(
        SimpleNamespace(gemini_backend="developer_api", gemini_api_key="test-key")
    )

    assert result is client
    constructor.assert_called_once_with(api_key="test-key")


def test_enterprise_client_uses_adc_project_and_region_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    constructor = Mock(return_value=client)
    _install_fake_genai(monkeypatch, constructor)

    result = create_genai_client(
        SimpleNamespace(
            gemini_backend="enterprise",
            gemini_api_key="must-not-be-used",
            gcp_project_id="spillthereel-test",
            gemini_location="global",
        )
    )

    assert result is client
    constructor.assert_called_once_with(
        enterprise=True,
        project="spillthereel-test",
        location="global",
    )


def test_vertex_api_key_client_uses_vertex_mode_and_key_without_adc_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    constructor = Mock(return_value=client)
    _install_fake_genai(monkeypatch, constructor)

    result = create_genai_client(
        SimpleNamespace(
            gemini_backend="vertex_api_key",
            gemini_api_key="vertex-test-key",
            gcp_project_id="must-not-be-used",
            gemini_location="must-not-be-used",
        )
    )

    assert result is client
    constructor.assert_called_once_with(vertexai=True, api_key="vertex-test-key")


def test_vertex_api_key_embeddings_use_adc_capable_vertex_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    constructor = Mock(return_value=client)
    _install_fake_genai(monkeypatch, constructor)

    result = create_embedding_client(
        SimpleNamespace(
            gemini_backend="vertex_api_key",
            gemini_api_key="vertex-test-key",
            gcp_project_id="spillthereel-test",
            gemini_location="global",
        )
    )

    assert result is client
    constructor.assert_called_once_with(
        enterprise=True,
        project="spillthereel-test",
        location="global",
    )



@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (SimpleNamespace(gemini_backend="developer_api", gemini_api_key=None), "gemini_api_key"),
        (SimpleNamespace(gemini_backend="vertex_api_key", gemini_api_key=None), "gemini_api_key"),
        (
            SimpleNamespace(gemini_backend="enterprise", gcp_project_id=None, gemini_location="global"),
            "gcp_project_id",
        ),
        (
            SimpleNamespace(gemini_backend="enterprise", gcp_project_id="test", gemini_location=None),
            "gemini_location",
        ),
        (SimpleNamespace(gemini_backend="mystery"), "gemini_backend"),
    ],
)
def test_client_factory_rejects_incomplete_or_unsupported_settings(
    settings: SimpleNamespace, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        create_genai_client(settings)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gemini-2.5-flash", 0.3),
        ("gemini-3.8-flash", None),
        ("publishers/google/models/gemini-3.8-flash", None),
        ("gemini-3-flash-preview", None),
    ],
)
def test_temperature_omitted_for_gemini_3_models(model: str, expected: float | None) -> None:
    assert generation_temperature(model, 0.3) == expected
