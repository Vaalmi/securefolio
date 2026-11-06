"""Settings, Keys: a key per provider, kept only in the Keychain and shown by its
last four characters, its optional Workspace ID or Project ID, and OpenRouter's
model list. Also each task's models, by provider, with what each costs."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend import (
    agent_service,
    keychain,
    model_catalog,
    policy,
    providers,
    refusals,
    settings,
)
from backend.dependencies import AppToken

router = APIRouter()
NO_SUCH_PROVIDER = "There's no such provider."
NO_ID = "This provider's key takes no ID."
PASTE_FIRST = "Paste a key first."
KEY_CHANGED = "The key or its ID changed while it was tested. Test it again."


class KeyTest(BaseModel):
    key: str | None = None  # none: test the saved key
    scope_id: str | None = None  # none: the saved ID, if any


class ScopeId(BaseModel):
    scope_id: str  # empty clears it


def _provider(provider: str) -> providers.Provider:
    found = providers.PROVIDERS.get(provider)
    if found is None:
        raise refusals.not_found(NO_SUCH_PROVIDER)
    return found


def _state(found: providers.Provider) -> dict[str, Any]:
    """What Settings shows for one provider's key: never the key itself."""
    return {
        "provider": found.id,
        "name": found.name,
        "present": keychain.get_key(found.id) is not None,
        "hint": keychain.hint(found.id),
        "id_label": found.id_label,
        "scope_id": settings.get(found.id_setting) if found.id_setting else None,
        # A saved key that its test found needs its ID: kept until one is added.
        "needs_id": policy.needs_its_id(found.id),
    }


@router.get("/settings/keys", operation_id="list_keys")
def list_keys(_: AppToken) -> list[dict[str, Any]]:
    """Each provider's key: whether there is one, and its last four characters."""
    return [_state(found) for found in providers.PROVIDERS.values()]


@router.post("/settings/keys/{provider}/test", operation_id="test_provider_key")
def check_provider_key(provider: str, body: KeyTest, _: AppToken) -> dict[str, Any]:
    """Tests a key with one tiny request, and stores a new one only once it works."""
    # A key that needs its ID is refused with 422, any other refusal with 409.
    found = _provider(provider)
    # None given: the saved key, or ID, read as the test starts.
    key = (body.key or "").strip() or None
    scope = body.scope_id.strip() if body.scope_id is not None else None
    try:
        agent_service.check_and_store_key(
            key, scope or None, found.id, saved_id=body.scope_id is None
        )
    except agent_service.NO_KEY_TO_TEST as none:
        raise refusals.invalid(PASTE_FIRST) from none
    except agent_service.KEY_REFUSED as refused:
        raise refusals.conflict(
            f"{found.name} didn't accept this key. Check it and paste it again."
        ) from refused
    except agent_service.ID_NEEDED as needed:
        word = "project" if found.id_label == "Project ID" else "workspace"
        raise refusals.invalid(
            f"This key isn't tied to one {word}. Add its {found.id_label}, then "
            "Test again."
        ) from needed
    except agent_service.KEY_NOT_TESTED as failed:
        raise refusals.conflict(
            f"SecureFolio couldn't reach {found.name}, so the key wasn't tested."
        ) from failed
    except agent_service.KEY_CHANGED as changed:
        raise refusals.conflict(KEY_CHANGED) from changed
    return _state(found)


@router.put("/settings/keys/{provider}/scope", operation_id="set_key_scope")
def set_key_scope(provider: str, body: ScopeId, _: AppToken) -> dict[str, Any]:
    """Sets the key's Workspace ID or Project ID, trimmed; empty clears it."""
    # The needs-ID mark is the key's: an ID hides its warning, and clearing the ID
    # brings it back.
    found = _provider(provider)
    if not found.id_setting:
        raise refusals.invalid(NO_ID)
    agent_service.set_key_scope(found.id, body.scope_id.strip() or None)
    return _state(found)


@router.delete("/settings/keys/{provider}", operation_id="remove_key")
def remove_key(provider: str, _: AppToken) -> dict[str, Any]:
    """The key leaves the Keychain, and its ID and needs-ID mark go with it."""
    found = _provider(provider)
    agent_service.remove_key(found.id)
    return _state(found)


@router.get("/models/openrouter", operation_id="get_openrouter_list")
def get_openrouter_list(_: AppToken) -> dict[str, Any]:
    """When OpenRouter's list was last stored, and why its last fetch failed, if so."""
    return model_catalog.openrouter_state()


@router.post("/models/openrouter/refresh", operation_id="refresh_openrouter_list")
def refresh_openrouter_list(_: AppToken) -> dict[str, Any]:
    """Fetches OpenRouter's list again; a failed fetch keeps the old one."""
    # Refused in Offline mode.
    if settings.get(settings.OFFLINE):
        raise refusals.conflict(
            "Offline mode is on, so OpenRouter's list isn't fetched."
        )
    try:
        agent_service.fetch_openrouter_models()
    except agent_service.KEY_NOT_TESTED as failed:
        raise refusals.conflict(str(failed)) from failed
    return model_catalog.openrouter_state()


def _model(model: model_catalog.Model) -> dict[str, Any]:
    return {
        "id": model.id,
        "name": model.name,
        "provider": model.provider,
        "input_usd": model.input_usd,
        "output_usd": model.output_usd,
        "measured": model.measured,
        "listed": model.listed,
    }


def _task(key: str, task: model_catalog.Task) -> dict[str, Any]:
    chosen = str(settings.get(key))
    models = model_catalog.for_task(task)
    found = model_catalog.get(chosen)
    if found is not None and found not in models:
        models.append(found)  # chosen, then left out of OpenRouter's list: it says so
    return {
        "task": task,
        "field": key,
        "chosen": chosen,
        "may_run_locally": key in model_catalog.MAY_RUN_LOCALLY,
        "models": [_model(model) for model in models],
    }


@router.get("/models/tasks", operation_id="list_task_models")
def list_task_models(_: AppToken) -> dict[str, Any]:
    """Each task's models by provider, with prices, and each provider's key state."""
    return {
        "providers": [
            {
                "id": found.id,
                "name": found.name,
                "key_ready": policy.key_ready(found.id),
                "id_label": found.id_label,
                "needs_id": policy.needs_its_id(found.id),
            }
            for found in providers.PROVIDERS.values()
        ],
        "tasks": [_task(key, task) for key, task in model_catalog.TASKS.items()],
    }
