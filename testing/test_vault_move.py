"""DocSynth's library becomes SecureFolio's on the app's first start: the folder
moves, the paths stored into it follow, and the cloud keys are copied. It never moves
while DocSynth holds it, over a library SecureFolio already has, or when the
environment names the vault; a move cut short finishes on the next start; and the
library stays locked from before the move until the backend's startup takes over."""

import sqlite3
from pathlib import Path
from typing import IO

import pytest
import sqlalchemy as sa

from backend import (
    database,
    documents,
    keychain,
    providers,
    rebase,
    settings,
    states,
    vault,
    vault_lock,
    vault_move,
)
from backend import main as backend_main
from testing.queue import drain


@pytest.fixture
def old_and_new(monkeypatch, tmp_path) -> tuple[Path, Path]:
    """DocSynth's folder is the test's vault; SecureFolio's, beside it, isn't yet."""
    old = vault.root()
    new = tmp_path / "SecureFolio"
    monkeypatch.setattr(vault, "LEGACY_VAULT", old)
    monkeypatch.setattr(vault, "DEFAULT_VAULT", new)
    return old, new


@pytest.fixture
def dropped(library, tmp_path) -> None:
    """A file copied into DocSynth's library, and its backups kept inside it."""
    source = tmp_path / "Dropped.txt"
    source.write_text("Copied in before the rename.\n")
    documents.import_paths([str(source)], states.STANDARD)
    drain()
    settings.put(settings.BACKUP_FOLDER, str(vault.backups_dir()))


def _as_the_app(monkeypatch) -> None:
    """No variable names a vault, as in the installed app."""
    database.dispose()
    monkeypatch.delenv(vault.VAULT_ENV, raising=False)
    monkeypatch.delenv(vault.LEGACY_VAULT_ENV, raising=False)


def _adopt() -> bool:
    held = vault_move.adopt_docsynth_library()
    if held is None:
        return False
    held.close()
    return True


def _stored_paths() -> list[str]:
    with database.read() as connection:
        locations = list(
            connection.execute(
                sa.select(database.document_locations.c.path).where(
                    database.document_locations.c.source_id == states.LIBRARY_SOURCE_ID
                )
            ).scalars()
        )
        home = connection.execute(
            sa.select(database.sources.c.path).where(
                database.sources.c.id == states.LIBRARY_SOURCE_ID
            )
        ).scalar_one()
        searched = list(
            connection.exec_driver_sql("SELECT path FROM documents_fts").scalars()
        )
    return [*locations, home, *searched, settings.get(settings.BACKUP_FOLDER)]


def _all_under(paths: list[str], root: Path) -> bool:
    return bool(paths) and all(path.startswith(str(root)) for path in paths)


def test_the_library_moves_and_every_stored_path_follows(
    dropped, old_and_new, monkeypatch
):
    old, new = old_and_new
    _as_the_app(monkeypatch)
    assert _adopt() is True
    assert not old.exists()
    assert (new / "library.db").is_file()
    assert not (new / vault_move.MARKER).exists()
    paths = _stored_paths()
    assert _all_under(paths, new)
    assert _adopt() is False  # once only


def test_a_move_cut_short_finishes_on_the_next_start(dropped, old_and_new, monkeypatch):
    old, new = old_and_new
    _as_the_app(monkeypatch)
    real = rebase.library_paths

    def crash(_moves: list[rebase.Move]) -> None:
        raise KeyboardInterrupt  # the process killed part way

    monkeypatch.setattr(rebase, "library_paths", crash)
    with pytest.raises(KeyboardInterrupt):
        vault_move.adopt_docsynth_library()
    assert (new / vault_move.MARKER).is_file()
    assert not old.exists()
    monkeypatch.setattr(rebase, "library_paths", real)
    database.dispose()
    assert _adopt() is True
    assert not (new / vault_move.MARKER).exists()
    assert _all_under(_stored_paths(), new)


def test_a_keychain_failure_is_tried_again_next_start(
    dropped, old_and_new, monkeypatch
):
    _, new = old_and_new
    _as_the_app(monkeypatch)
    tries: list[int] = []

    def flaky() -> None:
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("the Keychain is locked")

    monkeypatch.setattr(keychain, "copy_legacy_keys", flaky)
    with pytest.raises(RuntimeError):
        vault_move.adopt_docsynth_library()
    database.dispose()
    assert _adopt() is True
    assert len(tries) == 2
    assert not (new / vault_move.MARKER).exists()


def test_the_library_stays_locked_until_startup_takes_it(
    dropped, old_and_new, monkeypatch
):
    _, new = old_and_new
    _as_the_app(monkeypatch)
    seen: list[IO[str] | None] = []
    real = rebase.library_paths

    def look(moves: list[rebase.Move]) -> None:
        seen.append(vault_lock.try_hold(new / vault.LOCK_NAME))
        real(moves)

    monkeypatch.setattr(rebase, "library_paths", look)
    held = vault_move.adopt_docsynth_library()
    assert held is not None
    assert seen == [None]  # nobody else could take it while paths moved
    assert vault_lock.try_hold(new / vault.LOCK_NAME) is None  # still held now
    holding = vault_lock.acquire(held)  # the backend keeps it, never letting go
    holding.release()


def test_a_linked_library_folder_keeps_its_place(
    dropped, old_and_new, monkeypatch, tmp_path
):
    old, new = old_and_new
    elsewhere = tmp_path / "Volumes" / "Data" / "DocSynth"
    elsewhere.parent.mkdir(parents=True)
    database.dispose()
    old.rename(elsewhere)
    linked = tmp_path / "Application Support" / "DocSynth"
    linked.parent.mkdir()
    linked.symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(vault, "LEGACY_VAULT", linked)
    db = sqlite3.connect(elsewhere / "library.db")
    with db:  # paths stored through the link, as the app stores them
        for table in ("document_locations", "sources"):
            db.execute(
                f"UPDATE {table} SET path = replace(path, ?, ?)",  # noqa: S608
                (str(old), str(linked)),
            )
        db.execute(
            "UPDATE settings SET value = replace(value, ?, ?)", (str(old), str(linked))
        )
    db.close()
    _as_the_app(monkeypatch)
    assert _adopt() is True
    assert new.is_symlink()
    assert new.resolve() == elsewhere.resolve()
    assert _all_under(_stored_paths(), new)


def test_a_docsynth_stopped_on_its_first_start_still_moves(old_and_new, monkeypatch):
    old, new = old_and_new
    (old / "models").mkdir()  # the model cache comes before the database
    assert not (old / "library.db").exists()
    _as_the_app(monkeypatch)
    assert _adopt() is True
    assert (new / "library.db").is_file()
    assert not (new / vault_move.MARKER).exists()
    assert _adopt() is False


def test_nothing_moves_while_docsynth_holds_its_library(
    library, old_and_new, monkeypatch
):
    old, new = old_and_new
    _as_the_app(monkeypatch)
    held = vault_lock.try_hold(old / vault.LOCK_NAME)
    assert held is not None
    with held, pytest.raises(SystemExit, match="DocSynth is still running"):
        vault_move.adopt_docsynth_library()
    assert (old / "library.db").is_file()
    assert not (old / vault_move.MARKER).exists()
    assert not new.exists()


def test_a_library_securefolio_already_has_is_kept(library, old_and_new, monkeypatch):
    old, new = old_and_new
    new.mkdir()
    (new / "library.db").write_text("SecureFolio's own")
    _as_the_app(monkeypatch)
    assert _adopt() is False
    assert (old / "library.db").is_file()
    assert (new / "library.db").read_text() == "SecureFolio's own"


def test_an_empty_folder_or_link_left_for_securefolio_is_taken_over(
    library, old_and_new, monkeypatch, tmp_path
):
    old, new = old_and_new
    empty = tmp_path / "empty"
    empty.mkdir()
    new.symlink_to(empty, target_is_directory=True)
    _as_the_app(monkeypatch)
    assert _adopt() is True
    assert (new / "library.db").is_file()
    assert not new.is_symlink()
    assert not old.exists()


def test_a_vault_named_by_the_environment_never_moves(library, old_and_new):
    old, new = old_and_new
    assert _adopt() is False
    assert (old / "library.db").is_file()
    assert not new.exists()


def test_the_old_variable_still_names_a_development_vault(monkeypatch, tmp_path):
    monkeypatch.delenv(vault.VAULT_ENV)
    monkeypatch.setenv(vault.LEGACY_VAULT_ENV, str(tmp_path / "dev"))
    assert vault.root() == tmp_path / "dev"


def test_the_keys_are_copied_and_docsynths_stay(monkeypatch):
    anthropic = providers.PROVIDERS[providers.ANTHROPIC].account
    openai = providers.PROVIDERS[providers.OPENAI].account
    openrouter = providers.PROVIDERS[providers.OPENROUTER].account
    store = {
        (keychain.LEGACY_SERVICE, anthropic): "sk-ant-old",
        (keychain.LEGACY_SERVICE, openai): "sk-proj-old",
        (keychain.SERVICE, openai): "sk-proj-new",
    }
    monkeypatch.setattr(keychain, "_use_stand_in", lambda: False)
    monkeypatch.setattr(
        keychain.keyring, "get_password", lambda s, a: store.get((s, a))
    )
    monkeypatch.setattr(
        keychain.keyring, "set_password", lambda s, a, k: store.__setitem__((s, a), k)
    )
    keychain.copy_legacy_keys()
    assert store[(keychain.SERVICE, anthropic)] == "sk-ant-old"
    assert store[(keychain.SERVICE, openai)] == "sk-proj-new"  # never overwritten
    assert store[(keychain.LEGACY_SERVICE, anthropic)] == "sk-ant-old"
    assert (keychain.SERVICE, openrouter) not in store


def test_the_backend_moves_the_library_before_it_opens_the_vault(monkeypatch):
    calls: list[str] = []

    def adopt() -> None:
        calls.append("adopt")
        raise SystemExit("stop here")

    monkeypatch.setattr(vault_move, "adopt_docsynth_library", adopt)
    monkeypatch.setattr(vault, "use_model_cache", lambda: calls.append("cache"))
    with pytest.raises(SystemExit):
        backend_main.main()
    assert calls == ["adopt"]
