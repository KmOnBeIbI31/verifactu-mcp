"""
Aísla la DB de estado en cada test para evitar contaminar el disco del usuario
y garantizar determinismo entre tests.
"""
import os
import pytest


@pytest.fixture(autouse=True)
def _isolated_state_db(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    monkeypatch.setenv("VERIFACTU_STATE_DB", str(db))
    yield db
