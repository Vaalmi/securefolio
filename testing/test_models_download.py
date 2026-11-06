"""`npm run models` waits for the app's own download thread, picks up what an earlier
run left part way, and says which model didn't finish, even when the thread died."""

from backend import model_store
from scripts.models import download

ROW = {"bytes_done": 1, "bytes_total": 2, "reason": None}


def _fake(
    monkeypatch, busy_polls: int, final: list[str], reason: str | None = None
) -> list[str]:
    calls: list[str] = []
    polls = {"left": 0}

    def launch(name: str):
        def run() -> None:
            calls.append(name)
            polls["left"] = busy_polls

        return run

    def downloading() -> bool:
        polls["left"] -= 1
        return polls["left"] >= 0

    rows = [
        {**ROW, "model": f"m{i}", "state": s, "reason": reason if i == 0 else None}
        for i, s in enumerate(final)
    ]
    monkeypatch.setattr(download.vault, "use_model_cache", lambda: None)
    monkeypatch.setattr(download.migrations, "upgrade", lambda: None)
    monkeypatch.setattr(model_store, "resume_at_startup", launch("resume"))
    monkeypatch.setattr(model_store, "start_download", launch("start"))
    monkeypatch.setattr(model_store, "downloading", downloading)
    monkeypatch.setattr(model_store, "status", lambda: rows)
    monkeypatch.setattr(download, "POLL_SECONDS", 0)
    return calls


def test_it_resumes_then_downloads_and_waits_for_both(monkeypatch, capsys):
    calls = _fake(monkeypatch, 2, [model_store.INSTALLED, model_store.INSTALLED])
    assert download.main() == 0
    assert calls == ["resume", "start"]
    out = capsys.readouterr().out
    assert out.count("of 0.00 GB") == 4  # two polls while each was running
    assert "All models are in" in out


def test_a_failed_model_is_named_and_fails_the_command(monkeypatch, capsys):
    _fake(monkeypatch, 0, [model_store.FAILED, model_store.INSTALLED], "No connection")
    assert download.main() == 1
    assert "m0: No connection" in capsys.readouterr().err


def test_a_model_left_mid_way_by_a_dead_thread_ends_the_wait(monkeypatch, capsys):
    _fake(monkeypatch, 1, [model_store.VERIFYING])
    assert download.main() == 1
    assert "m0: stopped while verifying" in capsys.readouterr().err
