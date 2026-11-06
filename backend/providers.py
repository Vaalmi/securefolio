"""The cloud providers, as data. Every other module asks here rather than naming one."""

from dataclasses import dataclass
from typing import Literal

from backend import build_mode, settings

ANTHROPIC = "anthropic"
OPENAI = "openai"
OPENROUTER = "openrouter"
SEPARATOR = ":"  # a model id is "<provider>:<model>", as init_chat_model takes it

Citations = Literal["search_result", "markers"]


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    account: str  # its key's Keychain account
    host: str
    # Named here, never left to the SDKs, which would read OPENAI_BASE_URL or
    # ANTHROPIC_BASE_URL from the environment and send to wherever it says.
    base_url: str
    # Whether its cards go by a batch API, at half price: Anthropic's and OpenAI's.
    # OpenRouter has none, so its backlog is written one card at a time, at full price.
    batch: bool
    citations: Citations
    stub_env: str  # the variable naming its stub server, in the checks
    id_label: str | None = None  # the optional ID that scopes a key
    id_setting: str | None = None
    id_header: str | None = None
    needs_id_setting: str | None = None  # a saved key that needs an ID it lacks


PROVIDERS: dict[str, Provider] = {
    ANTHROPIC: Provider(
        ANTHROPIC,
        "Anthropic",
        "anthropic-api-key",
        "api.anthropic.com",
        "https://api.anthropic.com",
        batch=True,
        citations="search_result",
        stub_env=build_mode.STUB_ANTHROPIC_ENV,
        id_label="Workspace ID",
        id_setting=settings.ANTHROPIC_WORKSPACE,
        id_header="anthropic-workspace-id",
        needs_id_setting=settings.ANTHROPIC_NEEDS_ID,
    ),
    OPENAI: Provider(
        OPENAI,
        "OpenAI",
        "openai-api-key",
        "api.openai.com",
        "https://api.openai.com/v1",
        batch=True,
        citations="markers",
        stub_env=build_mode.STUB_OPENAI_ENV,
        id_label="Project ID",
        id_setting=settings.OPENAI_PROJECT,
        id_header="OpenAI-Project",
        needs_id_setting=settings.OPENAI_NEEDS_ID,
    ),
    OPENROUTER: Provider(
        OPENROUTER,
        "OpenRouter",
        "openrouter-api-key",
        "openrouter.ai",
        "https://openrouter.ai/api/v1",
        batch=False,
        citations="markers",
        stub_env=build_mode.STUB_OPENROUTER_ENV,
    ),
}


class UnknownProvider(ValueError):
    """A model id that names no provider SecureFolio knows."""


def split(model_id: str) -> tuple[str, str]:
    """`anthropic:claude-sonnet-5-5` into its provider and the provider's own name."""
    provider, found, name = model_id.partition(SEPARATOR)
    if not found or provider not in PROVIDERS or not name:
        raise UnknownProvider(model_id)
    return provider, name


def join(provider: str, name: str) -> str:
    return f"{provider}{SEPARATOR}{name}"


def of(model_id: str) -> Provider:
    return PROVIDERS[split(model_id)[0]]


def is_model_id(value: str) -> bool:
    try:
        split(value)
    except UnknownProvider:
        return False
    return True
