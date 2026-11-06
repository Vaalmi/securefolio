"""The only code that handles a document's password. Used in memory, once a job
chain has finished with it, and never written anywhere."""

import io
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import msoffcrypto
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

WAIT_SECONDS = 5.0
OFFICE_KINDS = {"DOCX", "XLSX", "PPTX"}


class WrongPassword(Exception):
    """The password didn't open this file."""


@dataclass
class _Box:
    by_job: dict[str, str] = field(default_factory=dict)
    by_document: dict[str, str] = field(default_factory=dict)
    arrived: threading.Condition = field(default_factory=threading.Condition)


_box = _Box()


def receive(job_id: str, password: str) -> None:
    """An unlock line from the backend, held for the job it names."""
    with _box.arrived:
        _box.by_job[job_id] = password
        _box.arrived.notify_all()


def take_for_job(
    job_id: str, document_id: str, wait: float = WAIT_SECONDS
) -> str | None:
    """The password for this job, if it arrives. It stays held for the document's
    hard-pages job, then `forget` drops it."""
    deadline = time.monotonic() + wait
    with _box.arrived:
        while job_id not in _box.by_job and time.monotonic() < deadline:
            _box.arrived.wait(max(0.0, deadline - time.monotonic()))
        password = _box.by_job.pop(job_id, None)
        if password is not None:
            _box.by_document[document_id] = password
    return password


def held_for(document_id: str) -> str | None:
    return _box.by_document.get(document_id)


def forget_job(job_id: str) -> None:
    with _box.arrived:
        _box.by_job.pop(job_id, None)


def settle(
    job_is_live: Callable[[str], bool], has_live_work: Callable[[str], bool]
) -> None:
    """Each password lives exactly as long as work that needs it: one for a job waiting
    behind a long queue stays; one whose job or chain ended goes."""
    with _box.arrived:
        for job_id in list(_box.by_job):
            if not job_is_live(job_id):
                del _box.by_job[job_id]
        for document_id in list(_box.by_document):
            if not has_live_work(document_id):
                del _box.by_document[document_id]


def forget(document_id: str) -> None:
    _box.by_document.pop(document_id, None)


def needs_password(kind: str, data: bytes) -> bool:
    if kind == "PDF":
        return _pdf_is_locked(data)
    if kind in OFFICE_KINDS:
        return _office_is_locked(data)
    return False


def _pdf_is_locked(data: bytes) -> bool:
    try:
        reader = PdfReader(io.BytesIO(data))
        return reader.is_encrypted and reader.decrypt("") == 0
    except PdfReadError:
        return False


def _office_is_locked(data: bytes) -> bool:
    try:
        return msoffcrypto.OfficeFile(io.BytesIO(data)).is_encrypted()
    except Exception:  # noqa: BLE001 - a plain Office file isn't an OLE container
        return False


def decrypt(kind: str, data: bytes, password: str) -> bytes:
    return (
        _decrypt_pdf(data, password)
        if kind == "PDF"
        else _decrypt_office(data, password)
    )


def _decrypt_pdf(data: bytes, password: str) -> bytes:
    reader = PdfReader(io.BytesIO(data))
    if reader.decrypt(password) == 0:
        raise WrongPassword
    out = io.BytesIO()
    PdfWriter(clone_from=reader).write(out)
    return out.getvalue()


def _decrypt_office(data: bytes, password: str) -> bytes:
    office = msoffcrypto.OfficeFile(io.BytesIO(data))
    out = io.BytesIO()
    try:
        office.load_key(password=password, verify_password=True)  # pyright: ignore[reportCallIssue]
        office.decrypt(out)
    except msoffcrypto.exceptions.InvalidKeyError as error:
        raise WrongPassword from error
    return out.getvalue()
