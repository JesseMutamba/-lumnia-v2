import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets its own throwaway SQLite file; the real data/ dir is
    never touched by the suite."""
    monkeypatch.setenv("LUMNIA_DB", str(tmp_path / "test.db"))
