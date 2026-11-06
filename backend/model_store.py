"""The models SecureFolio downloads itself: a row per model, with progress, resuming
from partial files and verified against the checked-in manifest. Also the local model
through Ollama, by way of agent_service, and which local model suits this Mac's
memory."""

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa

from backend import build_mode, database, jobs, providers, refusals, settings, vault
from backend.database import model_downloads

logger = logging.getLogger(__name__)
NOT_INSTALLED = "not_installed"
DOWNLOADING = "downloading"
VERIFYING = "verifying"
INSTALLED = "installed"
FAILED = "failed"
MANIFEST = Path(__file__).with_name("model_manifest.json")
STUB_MODELS_ENV = "SECUREFOLIO_STUB_MODELS_URL"
HOST = "https://huggingface.co"
CHUNK = 1024 * 1024
FLUSH_SECONDS = 1.0
PAUSE_POLL_SECONDS = 0.05
TIMEOUT_SECONDS = 60
GIB = 1024**3
OFFLINE_REASON = "Offline mode is on, so models can't download."
MISMATCH = "A downloaded file didn't match, so it was removed. Try again."
NO_CONNECTION = "The download couldn't reach its server. It resumes when you try again."
NO_LOCAL_PULL = (
    "Ollama couldn't download the model. Check that it's running, and try again."
)
LOCAL_BY_MEMORY = ((48, "gemma4:26b"), (24, "gemma4:12b"), (0, "qwen3.5:9b"))
_lock = threading.Lock()
_running = threading.Event()
_stopping = threading.Event()


class _OfflineNow(Exception):
    pass


class _Stopped(Exception):
    pass


def manifest() -> list[dict[str, Any]]:
    return json.loads(MANIFEST.read_text())


def _home(repo: str) -> Path:
    # The layout Hugging Face's own offline lookup reads.
    return vault.model_cache() / "huggingface" / "hub" / _folder_name(repo)


def _folder_name(repo: str) -> str:
    return f"models--{repo.replace('/', '--')}"


def _target(model: dict[str, Any], file: dict[str, Any]) -> Path:
    return _home(model["repo"]) / "snapshots" / model["revision"] / file["path"]


def _on_disk(model: dict[str, Any]) -> bool:
    return all(
        path.is_file() and path.stat().st_size == file["size"]
        for file in model["files"]
        for path in [_target(model, file)]
    )


def _total(model: dict[str, Any]) -> int:
    return sum(file["size"] for file in model["files"])


def _rows() -> dict[str, dict[str, Any]]:
    """A row for each model, in step with what's on disk now: a restored library may say
    a model is installed on a Mac that hasn't got it."""
    wanted = manifest()
    with database.read() as connection:
        rows = {
            r["model"]: dict(r)
            for r in connection.execute(sa.select(model_downloads)).mappings()
        }
    for model in wanted:
        row = rows.get(model["repo"])
        if build_mode.uses_stub_models():
            if row is None or row["state"] != INSTALLED:
                rows[model["repo"]] = _save(model, INSTALLED, _total(model))
        elif row is None or row["state"] == INSTALLED and not _on_disk(model):
            rows[model["repo"]] = _save(model, NOT_INSTALLED, 0)
    return {model["repo"]: rows[model["repo"]] for model in wanted}


def _save(
    model: dict[str, Any], state: str, done: int, reason: str | None = None
) -> dict[str, Any]:
    row = {
        "model": model["repo"],
        "state": state,
        "bytes_done": done,
        "bytes_total": _total(model),
        "reason": reason,
        "updated_at": time.time(),
    }
    with database.write() as connection:
        connection.execute(
            model_downloads.delete().where(model_downloads.c.model == model["repo"])
        )
        connection.execute(model_downloads.insert().values(**row))
    return row


def status() -> list[dict[str, Any]]:
    return list(_rows().values())


def group_status(group: str) -> dict[str, Any]:
    """What the screens show: one line for all of a group's models."""
    in_group = {m["repo"] for m in manifest() if m["group"] == group}
    rows = [row for row in status() if row["model"] in in_group]
    states = {row["state"] for row in rows}
    state = INSTALLED
    for candidate in (FAILED, DOWNLOADING, VERIFYING, NOT_INSTALLED):
        if candidate in states:
            state = candidate
            break
    reasons = [row["reason"] for row in rows if row["reason"]]
    return {
        "state": state,
        "bytes_done": sum(row["bytes_done"] for row in rows),
        "bytes_total": sum(row["bytes_total"] for row in rows),
        "reason": reasons[0] if reasons else None,
    }


def downloading() -> bool:
    """Whether the download thread is running. It stops when its work is done, or
    when a model fails in a way it doesn't record."""
    return _running.is_set()


def search_ready() -> bool:
    return group_status("search")["state"] == INSTALLED


def all_ready() -> bool:
    return all(row["state"] == INSTALLED for row in status())


def start_download() -> None:
    if settings.get(settings.OFFLINE):
        raise refusals.conflict(OFFLINE_REASON)
    by_repo = {model["repo"]: model for model in manifest()}
    for repo, row in _rows().items():
        if row["state"] in (NOT_INSTALLED, FAILED):
            _save(by_repo[repo], DOWNLOADING, row["bytes_done"])
    _launch()


def resume_at_startup() -> None:
    """A download a restart found mid-way carries on from its partial files, unless
    Offline is on. A model already whole on disk is hashed, which needs no network."""
    by_repo = {model["repo"]: model for model in manifest()}
    work = False
    for repo, row in _rows().items():
        model = by_repo[repo]
        if row["state"] in (NOT_INSTALLED, FAILED) and _on_disk(model):
            _save(model, VERIFYING, _total(model))
            work = True
        elif row["state"] in (DOWNLOADING, VERIFYING):
            if settings.get(settings.OFFLINE):
                _save(model, FAILED, row["bytes_done"], OFFLINE_REASON)
            else:
                _save(model, DOWNLOADING, row["bytes_done"])
                work = True
    if work:
        _launch()


def pause() -> None:
    """Stops the download thread at its next write, for a restore. Its rows stay as they
    are, so resume_at_startup carries on afterward."""
    _stopping.set()
    while _running.is_set():
        time.sleep(PAUSE_POLL_SECONDS)
    _stopping.clear()


def _launch() -> None:
    with _lock:
        if _running.is_set():
            return
        _running.set()
    threading.Thread(target=_run_all, daemon=True, name="model-download").start()


def _run_all() -> None:
    try:
        run_downloads()
    finally:
        _running.clear()


def run_downloads() -> None:
    by_repo = {model["repo"]: model for model in manifest()}
    for repo, row in _rows().items():
        if _stopping.is_set():
            return
        if row["state"] == DOWNLOADING:
            _download_one(by_repo[repo])
        elif row["state"] == VERIFYING:
            _verify(by_repo[repo])


def _download_one(model: dict[str, Any]) -> None:
    client_done = {"bytes": 0}
    try:
        with httpx.Client(
            follow_redirects=True, timeout=TIMEOUT_SECONDS, trust_env=False
        ) as client:
            for file in model["files"]:
                _fetch(client, model, file, client_done)
        _save(model, VERIFYING, _total(model))
        _verify(model)
    except _Stopped:
        return
    except _OfflineNow:
        _save(model, FAILED, client_done["bytes"], OFFLINE_REASON)
    except (httpx.HTTPError, ConnectionError, OSError) as failed:
        logger.info("a model download stopped: %s", type(failed).__name__)
        _save(model, FAILED, client_done["bytes"], NO_CONNECTION)


def _url(model: dict[str, Any], file: dict[str, Any]) -> str:
    host = build_mode.stub_url(STUB_MODELS_ENV) or HOST
    return f"{host}/{model['repo']}/resolve/{model['revision']}/{file['path']}"


def _fetch(
    client: httpx.Client,
    model: dict[str, Any],
    file: dict[str, Any],
    done: dict[str, int],
) -> None:
    target = _target(model, file)
    if target.is_file() and target.stat().st_size == file["size"]:
        done["bytes"] += file["size"]
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.name}.part")
    have = part.stat().st_size if part.exists() else 0
    if have > file["size"]:
        part.unlink()
        have = 0
    if have < file["size"]:
        _receive(client, _url(model, file), part, _Progress(model, done, have))
    else:
        done["bytes"] += have  # killed after its last byte, before the rename
    part.replace(target)


def _receive(client: httpx.Client, url: str, part: Path, progress: "_Progress") -> None:
    """From where the partial file stops. A server that won't serve that range gets
    asked again from the start."""
    headers = {"Range": f"bytes={progress.have}-"} if progress.have else {}
    with client.stream("GET", url, headers=headers) as response:
        refused = response.status_code == httpx.codes.REQUESTED_RANGE_NOT_SATISFIABLE
        if not refused:
            response.raise_for_status()
            resumed = response.status_code == httpx.codes.PARTIAL_CONTENT
            _write_part(part, response, resumed, progress.advancer(resumed))
    if refused:
        part.unlink()
        progress.have = 0
        _receive(client, url, part, progress)


@dataclass
class _Progress:
    model: dict[str, Any]
    done: dict[str, int]
    have: int

    def advancer(self, resumed: bool) -> Callable[[int], None]:
        self.done["bytes"] += self.have if resumed else 0
        flushed = [time.monotonic()]

        def advance(count: int) -> None:
            self.done["bytes"] += count
            if _stopping.is_set():
                raise _Stopped
            if time.monotonic() - flushed[0] >= FLUSH_SECONDS:
                flushed[0] = time.monotonic()
                if settings.get(settings.OFFLINE):
                    raise _OfflineNow
                _save(self.model, DOWNLOADING, self.done["bytes"])

        return advance


def _write_part(
    part: Path, response: httpx.Response, resumed: bool, advance: Callable[[int], None]
) -> None:
    with part.open("ab" if resumed else "wb") as written:
        for block in response.iter_bytes():
            written.write(block)
            advance(len(block))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _verify(model: dict[str, Any]) -> None:
    for file in model["files"]:
        target = _target(model, file)
        if _sha256(target) != file["sha256"]:
            target.unlink()
            _save(model, FAILED, 0, MISMATCH)
            return
    for name, revision in model["refs"].items():
        ref = _home(model["repo"]) / "refs" / name
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(revision)
    _save(model, INSTALLED, _total(model))


# ---- this Mac's local model ----


# The one download from Ollama, by tag: idle, pulling, installed or failed.
_pull: dict[str, Any] = {
    "tag": None,
    "state": NOT_INSTALLED,
    "done": 0,
    "total": 0,
    "reason": None,
}
ANOTHER_PULL = "Another model is downloading. Wait for it to finish first."
# The check that no download runs and the claim of the one slot are taken together, so
# two pulls asked for at once can't both start. Nothing slow happens under it.
_pull_lock = threading.Lock()
OLLAMA_STOPPED = "Ollama isn't running on this Mac, so it can't download. Start Ollama."
NOT_AN_OLLAMA_TAG = "That's a cloud model. A download takes a model from Ollama."


class PullFailed(Exception):
    """Ollama couldn't download the model; the message is its reason, in plain words."""


# Ollama's list as `agent_service.py` last gave it, so policy can read it without the
# agent. The call itself still asks Ollama, live.
_ollama: dict[str, Any] = {"at": None, "asked": None, "models": [], "running": True}
_OLLAMA_LIST = threading.Lock()


def remember_ollama(
    models: list[dict[str, Any]], running: bool = True, asked: float | None = None
) -> None:
    """Each model as `agent/models.py` lists it: its name, and whether it's remote. An
    Ollama that didn't answer is remembered too, as running nothing. `asked` is when
    the request went (time.monotonic): an answer to an older request than the one kept
    is late, and kept out."""
    asked = time.monotonic() if asked is None else asked
    with _OLLAMA_LIST:
        if _ollama.get("asked") is not None and asked < _ollama["asked"]:
            return
        _ollama.update(
            at=time.time(), asked=asked, models=list(models), running=running
        )


def local_tag(job: str = settings.LOCAL_ANSWERS) -> str:
    """The Ollama model a local job uses: answers, cards or figures. `LOCAL_MODEL` in
    development stands for all three. The route and the calls both read it here, so
    what's checked is what runs."""
    return os.environ.get("LOCAL_MODEL") or str(settings.get(job))


def ollama_down() -> bool:
    """Ollama was asked, and didn't answer: no local model can run until it does."""
    return _ollama["at"] is not None and not _ollama["running"]


def ollama_listed_at() -> float | None:
    return _ollama["at"]


REMOTE_SUFFIXES = ("-cloud", ":cloud")  # Ollama's names for models on its servers


def runs_remotely(tag: str) -> bool:
    """A model on Ollama's servers, by its name or by Ollama's own mark: never local."""
    return tag.endswith(REMOTE_SUFFIXES) or any(
        model["name"] == tag and model.get("remote") for model in _ollama["models"]
    )


def installed(tag: str) -> bool | None:
    """Whether Ollama has this tag on this Mac; None until Ollama has been asked. A
    model on Ollama's servers never counts."""
    if _ollama["at"] is None:
        return None
    return any(
        model["name"] == tag and not model.get("remote") for model in _ollama["models"]
    )


# What each model can do, by Ollama's own `/api/show`, kept by tag with the digest it
# was asked for: a tag pulled again is asked again.
_capabilities: dict[str, tuple[str, list[str]]] = {}
# What each job needs of its model: every job text, and figures images as well.
JOB_NEEDS = {
    settings.LOCAL_ANSWERS: ("completion",),
    settings.LOCAL_CARDS: ("completion",),
    settings.LOCAL_FIGURES: ("completion", "vision"),
}
NO_TEXT = "{tag} doesn't generate text, so it can't do this job."
NO_IMAGES = "{tag} doesn't take images, so it can't read figures."
NOT_IN_OLLAMA = "{tag} isn't installed in Ollama. Download it first."
NOT_ASKED = "Ollama hasn't said what {tag} can do yet. Try again in a moment."
REMOTE_TAG = "{tag} runs on Ollama's servers, not on this Mac."


def capabilities_known(tag: str, digest: str) -> bool:
    found = _capabilities.get(tag)
    return found is not None and found[0] == digest


def remember_capabilities(tag: str, digest: str, found: list[str]) -> None:
    _capabilities[tag] = (digest, [str(name) for name in found])


def _digest(tag: str) -> str | None:
    """The digest Ollama's last list gave this tag; None when it isn't listed."""
    for model in _ollama["models"]:
        if model["name"] == tag:
            return str(model.get("digest") or "")
    return None


def capabilities(tag: str) -> list[str] | None:
    """What Ollama said this model can do, for the version it lists now; None until
    Ollama has answered for that version."""
    found = _capabilities.get(tag)
    if found is None or found[0] != _digest(tag):
        return None
    return list(found[1])


def job_problem(job: str, tag: str) -> str | None:
    """Why this tag can't do this job on this Mac, or None: as the local job table
    says, on Ollama's servers, not installed, or without what the job needs."""
    if runs_remotely(tag):
        return REMOTE_TAG.format(tag=tag)
    if installed(tag) is False:
        return NOT_IN_OLLAMA.format(tag=tag)
    can = capabilities(tag)
    if can is None:
        return NOT_ASKED.format(tag=tag)  # never accepted on a guess
    if "completion" not in can:
        return NO_TEXT.format(tag=tag)
    if "vision" in JOB_NEEDS[job] and "vision" not in can:
        return NO_IMAGES.format(tag=tag)
    return None


def offered_for(tag: str) -> list[str]:
    """The jobs a model is offered for: only those Ollama says it can do. An
    embedding-only model is listed, and offered for none."""
    if capabilities(tag) is None:
        return []
    return [job for job in settings.LOCAL_JOBS if job_problem(job, tag) is None]


def installed_models() -> dict[str, list[dict[str, Any]]]:
    """Ollama's list as last seen: the models on this Mac, with their size and what
    they can do, and apart, those on Ollama's servers, never used locally."""
    here, remote = [], []
    for model in _ollama["models"]:
        tag = str(model["name"])
        if model.get("remote") or runs_remotely(tag):
            remote.append({"name": tag})
            continue
        can = capabilities(tag)
        here.append(
            {
                "name": tag,
                "size": int(model.get("size") or 0),
                "text": None if can is None else "completion" in can,
                "images": None if can is None else "vision" in can,
                "jobs": offered_for(tag),
                "memory": memory_needed([tag]),  # while loaded, as the jobs' total is
            }
        )
    return {"local": here, "remote": remote}


def remember_loaded(loaded: dict[str, int]) -> None:
    """Ollama's own figure for each loaded model's memory, kept by tag after it
    unloads."""
    if not loaded:
        return
    kept = dict(settings.get(settings.LOCAL_MEMORY_GB) or {})
    kept.update({tag: round(size / GIB, 1) for tag, size in loaded.items()})
    settings.put(settings.LOCAL_MEMORY_GB, kept)


def memory_needed(tags: list[str]) -> dict[str, Any]:
    """The memory the chosen models need together: Ollama's figure for one that has
    loaded, or else its size on disk, and then the total is "about"."""
    kept = settings.get(settings.LOCAL_MEMORY_GB) or {}
    sizes = {str(m["name"]): int(m.get("size") or 0) for m in _ollama["models"]}
    total, about = 0.0, False
    for tag in sorted(set(tags)):
        if tag in kept:
            total += float(kept[tag])
        else:
            total += round(sizes.get(tag, 0) / GIB, 1)
            about = True
    return {"gb": round(total, 1), "about": about}


def local_work_can_go() -> None:
    """A cards or figures model was chosen, or a model downloaded: the work waiting for
    one is requeued by the worker now. One such job at a time is enough."""
    jobs.enqueue(jobs.LOCAL_READY, jobs.PRIORITY_HOUSEKEEPING, key=jobs.LOCAL_READY)


def pull_status() -> dict[str, Any]:
    return dict(_pull)


@dataclass(frozen=True)
class Pull:
    """What a download from Ollama needs: the fetch, whether Ollama answers now, and
    what to do once the model is in, such as listing Ollama's models again."""

    fetch: Callable[[str, Callable[[int, int], None]], None]
    ollama_running: bool = True
    after: Callable[[], None] | None = None


def _refuse_a_pull(tag: str, pull: Pull) -> None:
    """The download table's refusals: Offline, a cloud or remote tag, another download
    running, and Ollama stopped."""
    if settings.get(settings.OFFLINE):
        raise refusals.conflict(OFFLINE_REASON)
    if providers.is_model_id(tag) or tag == settings.LOCAL:
        raise refusals.invalid(NOT_AN_OLLAMA_TAG)
    if runs_remotely(tag):
        raise refusals.invalid(REMOTE_TAG.format(tag=tag))
    if _pull["state"] == DOWNLOADING:
        raise refusals.conflict(ANOTHER_PULL)
    if not pull.ollama_running:
        raise refusals.conflict(OLLAMA_STOPPED)


def start_local_pull(tag: str, pull: Pull) -> None:
    """Ollama does the fetching, and only Ollama, one tag at a time. Its progress lasts
    as long as this process: a restart asks /api/tags again, and the owner can pull
    again."""
    with _pull_lock:
        _refuse_a_pull(tag, pull)
        _pull.update(tag=tag, state=DOWNLOADING, done=0, total=0, reason=None)

    def advance(done: int, total: int) -> None:
        _pull.update(done=done, total=total)

    def run() -> None:
        try:
            pull.fetch(tag, advance)
        except PullFailed as failed:
            _pull.update(state=FAILED, reason=str(failed))
        except Exception as failed:  # noqa: BLE001  a thread of its own, with a reason
            logger.info("the local model's pull stopped: %s", type(failed).__name__)
            _pull.update(state=FAILED, reason=NO_LOCAL_PULL)
        else:
            _pull.update(state=INSTALLED)
            if pull.after is not None:
                pull.after()  # it joins the list, and the selectors it suits

    threading.Thread(target=run, daemon=True, name="local-model-pull").start()


def memory_gib() -> int:
    found = subprocess.run(  # noqa: S603
        ["/usr/sbin/sysctl", "-n", "hw.memsize"],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(found.stdout.strip() or 0) // GIB


def choose_local_model() -> None:
    """Once, on first start: the model that suits this Mac's memory, for every job."""
    table = database.settings
    with database.read() as connection:
        chosen = connection.execute(
            sa.select(table.c.key).where(table.c.key.in_(settings.LOCAL_JOBS))
        ).first()
    if chosen is None and not build_mode.uses_stub_models():
        tag = default_local_tag()
        for job in settings.LOCAL_JOBS:
            settings.put(job, tag)


def default_local_tag(memory: int | None = None) -> str:
    """As docs/TECH_STACK.md's table says."""
    gib = memory_gib() if memory is None else memory
    return next(tag for least, tag in LOCAL_BY_MEMORY if gib >= least)


def no_cloud_is_set() -> bool:
    """OLLAMA_NO_CLOUD belongs to the Ollama server. SecureFolio reads it, and only asks
    the owner to set it."""
    found = subprocess.run(  # noqa: S603
        ["/bin/launchctl", "getenv", "OLLAMA_NO_CLOUD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return found.stdout.strip() == "1"
