"""When a library's folder isn't where its records say: after restoring a backup from
another vault, or once DocSynth's library has moved to SecureFolio's folder. The paths
of files copied into the library follow it, with their search rows, and so does a
backup folder chosen inside it. Watched folders are the owner's, wherever they are,
and stay as they were."""

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import database, search_row, settings, states, vault
from backend.database import document_locations
from backend.database import sources as source_table

# A folder that moved: where the records say it was, and where it is now.
Move = tuple[str, str]


def _moved(path: str, moves: list[Move]) -> str | None:
    place = Path(path)
    for old, new in moves:
        if place.is_relative_to(old):
            return str(Path(new) / place.relative_to(old))
    return None


def _managed_copies(connection: Connection, moves: list[Move]) -> None:
    rows = connection.execute(
        sa.select(
            document_locations.c.id,
            document_locations.c.document_id,
            document_locations.c.path,
        ).where(document_locations.c.source_id == states.LIBRARY_SOURCE_ID)
    ).all()
    for row in rows:
        target = _moved(row.path, moves)
        if target:
            connection.execute(
                document_locations.update()
                .where(document_locations.c.id == row.id)
                .values(path=target)
            )
            search_row.refresh_in(connection, row.document_id)


def _library_source(connection: Connection, moves: list[Move]) -> None:
    home = connection.execute(
        sa.select(source_table.c.path).where(
            source_table.c.id == states.LIBRARY_SOURCE_ID
        )
    ).scalar()
    target = _moved(home, moves) if home else None
    if target:
        connection.execute(
            source_table.update()
            .where(source_table.c.id == states.LIBRARY_SOURCE_ID)
            .values(path=target)
        )


def recorded_root() -> str | None:
    """The vault folder the records name, as they name it, link and all: the folder
    holding the library source's objects."""
    with database.read() as connection:
        home = connection.execute(
            sa.select(source_table.c.path).where(
                source_table.c.id == states.LIBRARY_SOURCE_ID
            )
        ).scalar()
    if not home or Path(home).name != vault.OBJECTS_NAME:
        return None
    return str(Path(home).parent)


def library_paths(moves: list[Move]) -> None:
    """Points every stored path under a moved folder at where that folder is now:
    managed copies and their search rows, the library's own source, and the backup
    folder when it was chosen inside. Running it twice changes nothing more."""
    moves = [(old, new) for old, new in moves if old and Path(old) != Path(new)]
    if not moves:
        return
    with database.write() as connection:
        _managed_copies(connection, moves)
        _library_source(connection, moves)
        chosen = settings.get_in(connection, settings.BACKUP_FOLDER)
        target = _moved(chosen, moves) if chosen else None
        if target:
            settings.put_in(connection, settings.BACKUP_FOLDER, target)
