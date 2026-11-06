"""The agent's one way to the backend's service modules, in-process."""

from dataclasses import dataclass
from typing import Any

from backend import cards as _cards
from backend import connectivity as _connectivity
from backend import database as _database
from backend import egress as _egress
from backend import embedder as _embedder
from backend import keychain as _keychain
from backend import memories as _memories
from backend import model_catalog as _model_catalog
from backend import model_store as _model_store
from backend import policy as _policy
from backend import providers as _providers
from backend import refusals as _refusals
from backend import reranker as _reranker
from backend import search as _search
from backend import settings as _settings
from backend import spend as _spend
from backend import states as _states
from backend import threads as _threads
from backend import tokens as _tokens
from backend import vault as _vault
from backend.build_mode import (
    STUB_ANTHROPIC_ENV,
    STUB_OLLAMA_ENV,
    stub_url,
    uses_stub_models,
)
from backend.netguard import is_loopback_host as _is_loopback_host
from backend.policy import Part, PartKind, Purpose

states = _states
STANDARD = _states.STANDARD
PRIVATE = _states.PRIVATE
OFFLINE = _settings.OFFLINE
MODEL_ANSWER = _settings.MODEL_ANSWER
MODEL_DEEP = _settings.MODEL_DEEP
MODEL_HELPER = _settings.MODEL_HELPER
LOCAL_ANSWERS = _settings.LOCAL_ANSWERS
LOCAL_CARDS = _settings.LOCAL_CARDS
LOCAL_FIGURES = _settings.LOCAL_FIGURES
LOCAL = _settings.LOCAL
ANTHROPIC_WORKSPACE = _settings.ANTHROPIC_WORKSPACE
MEMORY_ON = _settings.MEMORY_ON
ASK_FIRST = _settings.ASK_FIRST

threads = _threads
memories = _memories
cards = _cards
search = _search
spend = _spend
policy = _policy
connectivity = _connectivity
providers = _providers
catalog = _model_catalog

__all__ = [
    "uses_stub_models",
    "Part",
    "PartKind",
    "Purpose",
    "cards",
    "connectivity",
    "memories",
    "policy",
    "search",
    "spend",
    "threads",
]


def spend_cap_key() -> str:
    return _settings.SPEND_CAP


def setting(key: str) -> Any:
    return _settings.get(key)


def ollama_stub_url() -> str | None:
    return stub_url(STUB_OLLAMA_ENV)


def anthropic_stub_url() -> str | None:
    return stub_url(STUB_ANTHROPIC_ENV)


def stub_url_of(provider: str) -> str | None:
    """A provider's stub server, in the checks; never in a release build."""
    return stub_url(_providers.PROVIDERS[provider].stub_env)


def address_of(provider: str) -> str:
    """Where a provider's requests go: its stub in the checks, otherwise its own
    address, named in backend/providers.py and never read from the environment."""
    return stub_url_of(provider) or _providers.PROVIDERS[provider].base_url


def keychain_from_environment() -> bool:
    return _keychain.from_environment()


def anthropic_key() -> str | None:
    return _keychain.get_key(_providers.ANTHROPIC)


def key(provider: str) -> str | None:
    return _keychain.get_key(provider)


def scope_id(provider: str) -> str | None:
    """The key's Workspace ID or Project ID, if the owner gave one."""
    found = _providers.PROVIDERS[provider].id_setting
    return (_settings.get(found) or None) if found else None


def route(mode: str, include_private: bool, thread_private: bool) -> Any:
    return _policy.route(mode, include_private, thread_private)


def unpriced(model_id: str) -> str | None:
    return _spend.unpriced(model_id)


Price = _spend.Price


def price_of(model_id: str) -> Price | None:
    """Read once per call, at its gate: the copy it carries to its record and cost."""
    return _spend.price_of(model_id)


def no_price(model_id: str) -> str:
    return _spend.no_price(model_id)


def cost_at(price: Price, input_tokens: int, output_tokens: int) -> float:
    return _spend.cost_at(price, input_tokens, output_tokens)


@dataclass(frozen=True)
class Quote:
    """A call's price, read once at its gate, and its estimate at that price."""

    price: Price
    estimate: float


REMOTE_SUFFIXES = _model_store.REMOTE_SUFFIXES
PullFailed = _model_store.PullFailed


def local_tag(job: str) -> str:
    return _model_store.local_tag(job)


def remember_ollama(
    models: list[dict[str, Any]], running: bool = True, asked: float | None = None
) -> None:
    _model_store.remember_ollama(models, running, asked)


def capabilities_known(tag: str, digest: str) -> bool:
    return _model_store.capabilities_known(tag, digest)


def remember_capabilities(tag: str, digest: str, found: list[str]) -> None:
    _model_store.remember_capabilities(tag, digest, found)


def remember_loaded(loaded: dict[str, int]) -> None:
    _model_store.remember_loaded(loaded)


def store_openrouter(found: list[dict[str, Any]]) -> None:
    _model_catalog.store_openrouter(found)


def openrouter_failed(reason: str) -> None:
    _model_catalog.openrouter_failed(reason)


def page_image_refusal(document_id: str) -> str | None:
    return _policy.page_image_refusal(document_id)


def refusal(parts: list[Part], thread_id: str | None) -> str | None:
    return _policy.refusal(parts, thread_id)


def page_max_tokens() -> int:
    return _spend.PAGE_OUTPUT_TOKENS


def page_model() -> str:
    return _settings.get(_settings.MODEL_PAGE)


def page_estimate(price: Price | None = None) -> float:
    return _spend.page_read_estimate(price)


def count_tokens(text: str) -> int:
    return _tokens.count(text)


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    return _spend.cost(model, input_tokens, output_tokens)


def log_attempt(
    model: str,
    purpose: str,
    document_ids: list[str],
    quote: Quote,
    *,
    thread_id: str | None = None,
) -> int:
    """The attempt, with the one price its call goes at."""
    return _egress.record(
        _spend.priced(
            _egress.Entry(
                model,
                purpose,
                _egress.ATTEMPTED,
                document_ids,
                cost_usd=quote.estimate,
                thread_id=thread_id,
            ),
            quote.price,
        )
    )


def log_background_attempt(
    model: str,
    purpose: str,
    document_ids: list[str],
    quote: Quote,
    *,
    thread_id: str | None = None,
) -> int | None:
    """None when the cap is reached: nothing was recorded and nothing may go."""
    return _spend.reserve(
        _spend.priced(
            _egress.Entry(
                model,
                purpose,
                _egress.ATTEMPTED,
                document_ids,
                cost_usd=quote.estimate,
                thread_id=thread_id,
            ),
            quote.price,
        )
    )


def log_sent(entry_id: int, model: str, usage: dict[str, int]) -> float:
    input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
    price = _egress.accepted_price(entry_id)
    spent = _spend.settled_cost(price, model, input_tokens, output_tokens)
    _egress.settle(
        entry_id,
        _egress.SENT,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=spent,
    )
    return spent


def log_blocked(entry_id: int, why: str) -> None:
    _egress.settle(entry_id, _egress.REFUSED, cost_usd=0.0, detail=why)


def log_rejected(entry_id: int, status: int) -> None:
    """The provider answered no, so nothing is owed."""
    _egress.settle(entry_id, _egress.FAILED, cost_usd=0.0, detail=f"HTTP {status}")


def log_never_connected(entry_id: int) -> None:
    _egress.settle(entry_id, _egress.FAILED, cost_usd=0.0, detail="no connection")


def log_refused(
    model: str,
    purpose: str,
    document_ids: list[str],
    why: str,
    thread_id: str | None = None,
) -> None:
    _egress.record(
        _egress.Entry(
            model,
            purpose,
            _egress.REFUSED,
            document_ids,
            detail=why,
            thread_id=thread_id,
        )
    )


def log_batch_attempt(
    model: str, document_ids: list[str], quote: Quote, submission_id: str
) -> int | None:
    """None when the cap is reached: nothing was recorded and nothing may go."""
    return _spend.reserve(
        _spend.priced(
            _egress.Entry(
                model,
                Purpose.CARD,
                _egress.ATTEMPTED,
                document_ids,
                cost_usd=quote.estimate,
                detail=f"batch {submission_id}",
            ),
            quote.price,
        )
    )


def settle_batch(submission_id: str, usage: dict[str, int]) -> None:
    """Stays an attempt at its estimate until results set the true cost, half price."""
    _egress.settle_open(
        f"batch {submission_id}",
        _egress.SENT,
        lambda model, price: (
            _spend.settled_cost(
                price, model, usage["input_tokens"], usage["output_tokens"]
            )
            / 2
        ),
        usage,
    )


def batch_never_accepted(submission_id: str) -> None:
    _egress.settle_open(
        f"batch {submission_id}",
        _egress.FAILED,
        lambda _model, _price: 0.0,
        {"input_tokens": 0, "output_tokens": 0},
    )


def is_loopback_host(host: str | None) -> bool:
    return host is not None and _is_loopback_host(host)


def embed_query(text: str) -> Any:
    return _embedder.embed_query(text)


def to_blob(vector: Any) -> bytes:
    return _embedder.to_blob(vector)


def from_blob(blob: bytes) -> Any:
    return _embedder.from_blob(blob)


def rerank(query: str, texts: list[str]) -> list[float]:
    return _reranker.score(query, texts)


def checkpoint_path() -> str:
    return str(_vault.library_db())


def write() -> Any:
    return _database.write()


def read() -> Any:
    return _database.read()


def refusals_conflict(detail: str) -> Exception:
    return _refusals.conflict(detail)
