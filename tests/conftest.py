import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets its own throwaway SQLite file and file store; the real
    data/ dir is never touched by the suite."""
    monkeypatch.setenv("LUMNIA_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("LUMNIA_FILES", str(tmp_path / "files"))
