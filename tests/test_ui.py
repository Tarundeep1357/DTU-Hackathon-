import os

from streamlit.testing.v1 import AppTest


def test_guest_walkthrough_and_simulation(tmp_path, monkeypatch):
    monkeypatch.setenv("EA_DATABASE_URL", f"sqlite:///{tmp_path / 'ui.db'}")
    monkeypatch.setenv("EA_STORAGE_DIR", str(tmp_path / "sources"))
    monkeypatch.setenv("GEMMA_BASE_URL", "")
    monkeypatch.setenv("GEMMA_MODEL_ID", "")
    from evidence_arena.cli import migrate
    migrate()
    app = AppTest.from_file("app.py", default_timeout=30).run()
    assert not app.exception
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert any("Contested" in h.value for h in app.subheader)
    assert any("Unverified" in h.value for h in app.subheader)
    assert any("PREPARED" in w.value for w in app.warning)
    assert [m.value for m in app.metric] == ["0", "0", "1", "1"]
    assert len(app.get("download_button")) >= 3

