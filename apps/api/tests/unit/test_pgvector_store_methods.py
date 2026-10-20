from app.services.memory.pgvector_store import PgVectorStore


def test_pgvector_store_implements_memory_store_methods() -> None:
    """Public protocol methods must remain class methods, not nested helpers."""
    for method_name in ("write_batch", "search", "similar", "delete", "delete_namespace"):
        assert callable(getattr(PgVectorStore, method_name, None)), method_name

