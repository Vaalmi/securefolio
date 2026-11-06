"""The cloud models a task may use: what each can do, what it costs, and whether the
evaluation has measured it. Claude's and OpenAI's are a checked list with dated
prices; OpenRouter's come from the snapshot of its own published list."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa

from backend import database, providers, settings
from backend.database import openrouter_models

PRICES_AS_OF = "2026-11-05"  # Anthropic's and OpenAI's published prices on this day
STALE_SECONDS = 7 * 24 * 3600
MEASURED = Path(__file__).parent / "model_measured.json"

Task = Literal["answer", "deep", "helper", "page"]
TASKS: dict[str, Task] = {
    settings.MODEL_ANSWER: "answer",
    settings.MODEL_DEEP: "deep",
    settings.MODEL_HELPER: "helper",
    settings.MODEL_PAGE: "page",
}
# Tasks that may be set to this Mac's model ("local"), as before the providers came.
MAY_RUN_LOCALLY = (settings.MODEL_ANSWER, settings.MODEL_HELPER)


@dataclass(frozen=True)
class Model:
    id: str  # provider:model
    name: str
    input_usd: float | None  # per million tokens; None: no known price
    output_usd: float | None
    images: bool
    structured: bool
    listed: bool = True  # False: OpenRouter's newest list no longer has it

    @property
    def provider(self) -> str:
        return providers.split(self.id)[0]

    @property
    def measured(self) -> bool:
        """Whether the evaluation has measured it as the Answer model: in
        `model_measured.json`, or not measured."""
        return measured(self.id)


def _checked(provider: str, name: str, label: str, price: tuple[float, float]) -> Model:
    return Model(providers.join(provider, name), label, *price, True, True)


# Every one takes images and returns structured output (each provider's model pages).
CHECKED: tuple[Model, ...] = (
    _checked(
        providers.ANTHROPIC, "claude-sonnet-5-5", "Claude Sonnet 5.5", (2.0, 10.0)
    ),
    _checked(providers.ANTHROPIC, "claude-opus-5-5", "Claude Opus 5.5", (4.0, 20.0)),
    _checked(providers.ANTHROPIC, "claude-haiku-5-5", "Claude Haiku 5.5", (0.10, 0.50)),
    _checked(providers.OPENAI, "gpt-6-astra", "GPT-6 Astra", (10.0, 50.0)),
    _checked(providers.OPENAI, "gpt-6.1-sol", "GPT-6.1 Sol", (2.0, 10.0)),
    _checked(providers.OPENAI, "gpt-6.1-terra", "GPT-6.1 Terra", (1.0, 5.0)),
    _checked(providers.OPENAI, "gpt-6-luna", "GPT-6 Luna", (0.10, 0.50)),
)
_CHECKED = {model.id: model for model in CHECKED}


def _from_row(row: Any) -> Model:
    return Model(
        providers.join(providers.OPENROUTER, row.id),
        row.name,
        row.input_usd,
        row.output_usd,
        bool(row.images),
        bool(row.structured),
        bool(row.listed),
    )


def get(model_id: str) -> Model | None:
    """The model, if SecureFolio knows it: checked, or in OpenRouter's stored list."""
    if model_id in _CHECKED:
        return _CHECKED[model_id]
    if not providers.is_model_id(model_id):
        return None
    provider, name = providers.split(model_id)
    if provider != providers.OPENROUTER:
        return None
    with database.read() as connection:
        row = connection.execute(
            sa.select(openrouter_models).where(openrouter_models.c.id == name)
        ).first()
    return _from_row(row) if row else None


def price(model_id: str) -> tuple[float, float] | None:
    """Input and output, per million tokens, or None when there's no known price."""
    model = get(model_id)
    if model is None or model.input_usd is None or model.output_usd is None:
        return None
    return model.input_usd, model.output_usd


def can_do(model: Model, task: Task) -> bool:
    """Reading a page takes images, and the helpers return structured output."""
    if task == "page":
        return model.images
    if task == "helper":
        return model.structured
    return True


def all_models() -> list[Model]:
    with database.read() as connection:
        rows = connection.execute(
            sa.select(openrouter_models).order_by(openrouter_models.c.name)
        ).all()
    return [*CHECKED, *(_from_row(row) for row in rows)]


def for_task(task: Task) -> list[Model]:
    return [
        model
        for model in all_models()
        if can_do(model, task) and model.listed and price(model.id) is not None
    ]


def measured(model_id: str) -> bool:
    """Read from beside this module, never from the checkout: the bundle carries it."""
    return model_id in json.loads(MEASURED.read_text())["models"]


def chosen() -> set[str]:
    """The models the tasks are set to: kept through every refresh of the list."""
    return {str(settings.get(key)) for key in settings.MODEL_TASKS}


def store_openrouter(found: list[dict[str, Any]]) -> None:
    """A fresh list: upserted, a chosen model it left out kept at its last price and
    marked no longer listed, any other dropped."""
    now = time.time()
    names = {row["id"] for row in found}
    with database.write() as connection:
        # Read under the write lock, so a model chosen while the list was fetched is
        # kept: no choice can be saved between this read and the deletes.
        keep = {
            providers.split(model)[1]
            for model in chosen()
            if providers.is_model_id(model)
            and providers.split(model)[0] == providers.OPENROUTER
        }
        for row in found:
            values = {**row, "listed": True, "fetched_at": now}
            connection.execute(
                openrouter_models.delete().where(openrouter_models.c.id == row["id"])
            )
            connection.execute(openrouter_models.insert().values(**values))
        gone = openrouter_models.c.id.not_in(names)
        connection.execute(
            openrouter_models.delete().where(gone, openrouter_models.c.id.not_in(keep))
        )
        connection.execute(openrouter_models.update().where(gone).values(listed=False))
    settings.put(settings.OPENROUTER_FETCHED, now)
    settings.put(settings.OPENROUTER_FAILED, None)


def openrouter_failed(reason: str) -> None:
    """A fetch that didn't work leaves the list as it was, and says why."""
    settings.put(settings.OPENROUTER_FAILED, reason)


ListState = Literal["none", "fresh", "stale"]


def openrouter_state(now: float | None = None) -> dict[str, Any]:
    fetched = settings.get(settings.OPENROUTER_FETCHED)
    state: ListState = "none"
    if fetched is not None:
        age = (now or time.time()) - float(fetched)
        state = "stale" if age >= STALE_SECONDS else "fresh"
    return {
        "state": state,
        "fetched_at": fetched,
        "failed": settings.get(settings.OPENROUTER_FAILED),
    }


def to_row(found: dict[str, Any]) -> dict[str, Any] | None:
    """One entry of OpenRouter's `/models` as a stored row, or None when unusable: no
    id, or no text out. Its prices are per token, as strings; ours are per million."""
    pricing = found.get("pricing") or {}
    architecture = found.get("architecture") or {}
    parameters = found.get("supported_parameters") or []
    try:
        input_usd = float(pricing["prompt"]) * 1_000_000
        output_usd = float(pricing["completion"]) * 1_000_000
    except (KeyError, TypeError, ValueError):
        input_usd = output_usd = None
    if (
        input_usd is not None
        and output_usd is not None
        and min(input_usd, output_usd) < 0
    ):
        input_usd = output_usd = None  # OpenRouter's mark for a price set per request
    if not found.get("id"):
        return None
    # Every job SecureFolio gives a model, a page read and the helpers' JSON included,
    # needs text back: a model that doesn't say it returns text is offered for none.
    if "text" not in (architecture.get("output_modalities") or []):
        return None
    return {
        "id": str(found["id"]),
        "name": str(found.get("name") or found["id"]),
        "input_usd": input_usd,
        "output_usd": output_usd,
        "images": "image" in (architecture.get("input_modalities") or []),
        "structured": "structured_outputs" in parameters,
    }
