"""Settings: the key, a model per task, Offline, spending and the cloud log, models,
backup, and pausing the queue from the menu bar."""

import asyncio
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend import (
    agent_service,
    backup,
    database,
    keychain,
    model_catalog,
    model_store,
    policy,
    providers,
    refusals,
    settings,
    spend,
    vault,
)
from backend.database import egress_log
from backend.dependencies import AppToken

router = APIRouter()
LOG_PAGE = 100
NOT_READY = (
    "Available once the search, OCR and page-reading models and the local model are "
    "installed."
)
KEY_REFUSED = (
    "Anthropic didn't accept this key. Check it in the Anthropic Console and paste it "
    "again."
)
WORKSPACE_NEEDED = (
    "This key isn't tied to one workspace. Add its Workspace ID from the Anthropic "
    "Console, and test again."
)
NO_REACH = "SecureFolio couldn't reach Anthropic, so the key wasn't tested."
KEY_CHANGED = "The key changed while it was tested. Test it again."
INDEXING_PAUSED = "indexing_paused"
WAIT_FOR_REQUESTS_SECONDS = 30
STILL_BUSY = "SecureFolio is busy, so the backup wasn't restored. Try again."
NOT_A_MODEL = "There's no such model for this task."
NO_PRICE = "This model has no known price, so it can't be chosen."
NO_KEY = "Add this model's provider key in Settings first."
CLOUD_AS_LOCAL = "A local job runs on this Mac, so it takes a model from Ollama."
REMOTE_AS_LOCAL = "That model runs on Ollama's servers, not on this Mac."


class Empty(BaseModel):
    pass


class SettingsPatch(BaseModel):
    model_answer: str | None = None
    model_deep: str | None = None
    model_helper: str | None = None
    model_page: str | None = None
    local_answers: str | None = None
    local_cards: str | None = None
    local_figures: str | None = None
    default_sensitivity: Literal["standard", "private"] | None = None
    spend_cap_usd: float | None = None
    setup_done: bool | None = None
    backup_nightly: bool | None = None
    backup_folder: str | None = None


class OfflineBody(BaseModel):
    on: bool


class KeyBody(BaseModel):
    key: str | None = None
    workspace: str | None = None


class RestoreBody(BaseModel):
    id: str


FIELDS = {
    "model_answer": settings.MODEL_ANSWER,
    "model_deep": settings.MODEL_DEEP,
    "model_helper": settings.MODEL_HELPER,
    "model_page": settings.MODEL_PAGE,
    "local_answers": settings.LOCAL_ANSWERS,
    "local_cards": settings.LOCAL_CARDS,
    "local_figures": settings.LOCAL_FIGURES,
    "default_sensitivity": settings.DEFAULT_SENSITIVITY,
    "spend_cap_usd": settings.SPEND_CAP,
    "setup_done": settings.SETUP_DONE,
    "backup_nightly": settings.BACKUP_NIGHTLY,
    "backup_folder": settings.BACKUP_FOLDER,
    "offline": settings.OFFLINE,
}


@router.get("/settings", operation_id="get_settings")
def get_settings(_: AppToken) -> dict[str, Any]:
    """Every setting the app shows."""
    return {name: settings.get(key) for name, key in FIELDS.items()}


@router.patch("/settings", operation_id="update_settings")
def update_settings(body: SettingsPatch, _: AppToken) -> Empty:
    """Takes effect on the next request, with no restart."""
    changes = body.model_dump(exclude_none=True)
    cap = changes.get("spend_cap_usd")
    if cap is not None and cap <= 0:
        raise refusals.invalid("Enter an amount in US dollars, such as 25.00.")
    for name, value in changes.items():
        key = FIELDS[name]
        if key in settings.LOCAL_JOBS:
            _check_local(key, str(value))  # asks Ollama, so never under the lock
    with database.write() as connection:
        # A model is checked and saved under one write lock, so a refresh of
        # OpenRouter's list can't drop it between the two.
        for name, value in changes.items():
            if FIELDS[name] in settings.MODEL_TASKS:
                _check_model(FIELDS[name], str(value))
        for name, value in changes.items():
            settings.put_in(connection, FIELDS[name], value)
    if {FIELDS[name] for name in changes} & {
        settings.LOCAL_CARDS,
        settings.LOCAL_FIGURES,
    }:
        model_store.local_work_can_go()  # pending work goes to the model just chosen
    return Empty()


def _check_model(key: str, model_id: str) -> None:
    """A task takes a model it can do, with a known price, from a provider whose key
    is ready; Answers and the helpers may also run on this Mac."""
    if model_id == settings.LOCAL and key in model_catalog.MAY_RUN_LOCALLY:
        return
    model = model_catalog.get(model_id)
    if model is None or not model.listed:
        raise refusals.invalid(NOT_A_MODEL)
    if not model_catalog.can_do(model, model_catalog.TASKS[key]):
        raise refusals.invalid(NOT_A_MODEL)
    if model_catalog.price(model_id) is None:
        raise refusals.invalid(NO_PRICE)
    if not policy.key_ready(model.provider):
        raise refusals.invalid(NO_KEY)


def _check_local(job: str, tag: str) -> None:
    """A local job's model is on this Mac: never a cloud model, never one on Ollama's
    servers, installed, and able to do the job, by Ollama's own capabilities."""
    if providers.is_model_id(tag) or tag == settings.LOCAL:
        raise refusals.invalid(CLOUD_AS_LOCAL)
    if model_store.runs_remotely(tag):
        raise refusals.invalid(REMOTE_AS_LOCAL)
    agent_service.refresh_ollama()
    problem = model_store.job_problem(job, tag)
    if problem is not None:
        raise refusals.invalid(problem)


def _offline_blocker() -> str | None:
    if model_store.all_ready() and agent_service.local_ready():
        return None
    return NOT_READY


@router.put("/settings/offline", operation_id="set_offline")
def set_offline(body: OfflineBody, _: AppToken) -> Empty:
    """Refused until every model is on this Mac, which only the backend decides."""
    if body.on:
        why = _offline_blocker()
        if why is not None:
            raise refusals.conflict(why)
    settings.put(settings.OFFLINE, body.on)
    return Empty()


@router.get("/settings/key", operation_id="get_key_status")
def get_key_status(_: AppToken) -> dict[str, Any]:
    """Whether there is a key, and its last four characters. Never the key."""
    return {
        "present": keychain.get_key() is not None,
        "hint": keychain.hint(),
        "workspace": settings.get(settings.ANTHROPIC_WORKSPACE),
    }


@router.post("/settings/key/test", operation_id="test_key")
def check_key(body: KeyBody, _: AppToken) -> Empty:
    """One tiny request. A new key is stored only once it has worked."""
    try:
        # With none given, the saved key or workspace is the one tested, read as the
        # test starts, and the workspace stays.
        agent_service.check_and_store_key(
            body.key or None, body.workspace or None, saved_id=not body.workspace
        )
    except agent_service.NO_KEY_TO_TEST as none:
        raise refusals.invalid("Paste a key first.") from none
    except agent_service.KEY_REFUSED as refused:
        raise refusals.conflict(KEY_REFUSED) from refused
    except agent_service.ID_NEEDED as needed:
        raise refusals.conflict(WORKSPACE_NEEDED) from needed
    except agent_service.KEY_NOT_TESTED as failed:
        raise refusals.conflict(NO_REACH) from failed
    except agent_service.KEY_CHANGED as changed:
        raise refusals.conflict(KEY_CHANGED) from changed
    return Empty()


@router.get("/spending", operation_id="get_spending")
def get_spending(_: AppToken, offset: int = 0) -> dict[str, Any]:
    """This month's spend, the cap, and every call, latest first."""
    with database.read() as connection:
        rows = connection.execute(
            sa.select(egress_log)
            .order_by(egress_log.c.at.desc())
            .limit(LOG_PAGE)
            .offset(max(offset, 0))
        ).mappings()
        log = [_named(dict(row)) for row in rows]
    return {
        "spent_usd": spend.spent_this_month(),
        "cap_usd": float(settings.get(settings.SPEND_CAP)),
        "prices_as_of": spend.PRICES_AS_OF,
        "log": log,
    }


def _named(call: dict[str, Any]) -> dict[str, Any]:
    """A logged call with its provider's and model's names, as the cloud log shows
    them, for every provider."""
    found = providers.PROVIDERS.get(call["provider"])
    model = model_catalog.get(call["model"])
    bare = (
        providers.split(call["model"])[1]
        if providers.is_model_id(call["model"])
        else call["model"]
    )
    return {
        **call,
        "provider_name": found.name if found else call["provider"],
        "model_name": model.name if model else bare,
    }


@router.get("/models", operation_id="get_models")
def get_models(_: AppToken) -> dict[str, Any]:
    """The downloaded models, the local model, and what Ollama says."""
    ollama = agent_service.ollama_status()
    tag = agent_service.local_tag(settings.LOCAL_ANSWERS)
    jobs = {
        job: {
            "tag": agent_service.local_tag(job),
            "problem": model_store.job_problem(job, agent_service.local_tag(job)),
        }
        for job in settings.LOCAL_JOBS
    }
    return {
        "jobs": jobs,
        "installed": model_store.installed_models(),
        "memory": model_store.memory_needed([job["tag"] for job in jobs.values()]),
        "mac_memory_gb": model_store.memory_gib(),
        "search": model_store.group_status("search"),
        "page": model_store.group_status("page"),
        "local": {
            "tag": tag,
            "recommended": model_store.default_local_tag(),
            "installed": tag in ollama["tags"],
            "pull": model_store.pull_status(),
        },
        "ollama": {**ollama, "no_cloud": model_store.no_cloud_is_set()},
    }


@router.post("/models/download", operation_id="download_models")
def download_models(_: AppToken) -> Empty:
    """Starts or resumes the downloads, carrying on in the background."""
    model_store.start_download()
    return Empty()


@router.post("/models/local/download", operation_id="download_local_model")
def download_local_model(_: AppToken) -> Empty:
    """Asks Ollama to pull the answers job's model."""
    model_store.start_local_pull(
        agent_service.local_tag(settings.LOCAL_ANSWERS), agent_service.local_pull()
    )
    return Empty()


class PullBody(BaseModel):
    tag: str


@router.post("/models/local/pull", operation_id="pull_local_model")
def pull_local_model(body: PullBody, _: AppToken) -> Empty:
    """Asks Ollama to pull any tag, one at a time, with progress by tag."""
    model_store.start_local_pull(body.tag.strip(), agent_service.local_pull())
    return Empty()


@router.get("/backups", operation_id="list_backups")
def list_backups(_: AppToken) -> dict[str, Any]:
    """The backup folder, how the last one went, and those kept."""
    return {
        "folder": str(backup.folder()),
        "last": settings.get(settings.BACKUP_LAST),
        "backups": backup.list_backups(),
    }


@router.post("/backups", operation_id="back_up_now")
def back_up_now(_: AppToken) -> dict[str, Any]:
    """Backs up now, and says how it went."""
    return backup.run_now()


def _restore_alone(backup_id: str) -> None:
    try:
        with database.exclusive(WAIT_FOR_REQUESTS_SECONDS):
            backup.restore(backup_id)
    except database.Sealed as busy:
        raise refusals.conflict(STILL_BUSY) from busy


@router.post("/backups/restore", operation_id="restore_backup")
async def restore_backup(body: RestoreBody, request: Request, _: AppToken) -> Empty:
    """Stops everything else that writes, then brings the library back."""
    state = request.app.state
    if not await state.gate.close(WAIT_FOR_REQUESTS_SECONDS):
        raise refusals.conflict(STILL_BUSY)
    try:
        await state.agent.close()
        await asyncio.to_thread(model_store.pause)
        await state.supervisor.stop()
        await asyncio.to_thread(_restore_alone, body.id)
    finally:
        await state.agent.open()
        model_store.resume_at_startup()
        state.supervisor.start()
        state.gate.open()
    return Empty()


@router.post("/indexing/toggle-pause", operation_id="toggle_indexing_pause")
def toggle_indexing_pause(_: AppToken) -> dict[str, bool]:
    """The menu bar's Pause. The worker reads it before each job."""
    paused = not settings.get(INDEXING_PAUSED)
    settings.put(INDEXING_PAUSED, paused)
    return {"paused": paused}


@router.get("/logs/folder", operation_id="get_log_folder")
def get_log_folder(_: AppToken) -> dict[str, str]:
    """Where the service log is, for Show in Finder."""
    return {"path": str(vault.logs_dir())}
