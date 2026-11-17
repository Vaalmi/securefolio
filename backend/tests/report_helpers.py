"""What the report tests share (`test_reports.py`, `test_privacy_reports.py`): a
folder with a lease and the roof report, a stub cloud model that answers, and the
calls that ask, report and read the saved set."""

import json
import shutil
from pathlib import Path

import sqlalchemy as sa

from backend import database, keychain, reports, sources, states
from backend.build_mode import STUB_ANTHROPIC_ENV
from testing.ask_helpers import ask, default_reply
from testing.queue import drain
from testing.stub_models import stub_anthropic

REPO = Path(__file__).parents[2]
DATA = REPO / "data" / "library"


def lease_and_roof(tmp_path: Path) -> None:
    root = tmp_path / "w"
    root.mkdir()
    (root / "Lease.txt").write_text(
        "Marker rent.\nThe rent is due monthly.\nIt is $2,350.\n"
    )
    shutil.copy(DATA / "Home" / "Roof inspection report.pdf", root / "Roof.pdf")
    sources.add_source(str(root), states.STANDARD)
    drain()


def cloud_answers(monkeypatch):
    keychain.set_key("test-not-a-real-key")
    with stub_anthropic() as stub:
        monkeypatch.setenv(STUB_ANTHROPIC_ENV, stub.url)
        stub.reply = default_reply
        yield stub


def document(title: str) -> str:
    with database.read() as connection:
        return connection.execute(
            sa.select(database.documents.c.id).where(
                database.documents.c.title == title
            )
        ).scalar_one()


def answered(app_client, thread: str, question: str) -> str:
    return ask(app_client, thread, question)["messages"][-1]["id"]


def report(app_client, thread: str, message: str, **said):
    return app_client.post(
        f"/api/threads/{thread}/messages/{message}/report", json=said
    )


def saved() -> list[dict]:
    return json.loads(reports.set_path().read_text())
