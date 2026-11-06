"""The month's spend from the egress log, the cap, and estimates, for every provider
together, priced through the model catalog."""

import datetime
from dataclasses import replace
from typing import TypedDict

from backend import egress, model_catalog, providers, settings

PRICES_AS_OF = model_catalog.PRICES_AS_OF
PER_MILLION = 1_000_000


class UnknownPrice(LookupError):
    """No known price for this model: nothing may be sent to it."""


Price = tuple[float, float]  # per million tokens, input and output


def price_of(model_id: str) -> Price | None:
    """A model's price now. A call reads it once, at its gate, and carries that copy
    through its estimate, its record and its cost."""
    return model_catalog.price(model_id)


def no_price(model_id: str) -> str:
    name = model_catalog.get(model_id)
    return f"No known price for {name.name if name else model_id}."


def unpriced(model_id: str) -> str | None:
    """Why no call may go to this model, asked before any call is built."""
    return no_price(model_id) if price_of(model_id) is None else None


def cost_at(price: Price, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * price[0] + output_tokens * price[1]) / PER_MILLION


EXCERPT_TOKENS = 6800
BATCH_ABOVE = 50  # more than this many waiting documents go by the Batches API
BATCH_SHARE = 0.5  # a batch API's price: half the full price, on both providers
# From a live batch of 122 cards on 2026-11-01: instructions and schema, and each reply.
CARD_OUTPUT_TOKENS = 215
CARD_INSTRUCTION_TOKENS = 980
# A letter page at the render size, and the most a reply may use, thinking included,
# so the cost shown is a ceiling.
PAGE_INPUT_TOKENS = 1600
PAGE_OUTPUT_TOKENS = 8192


def cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Checked with `unpriced` first, so an unknown price stops a call before it's
    built; once a call has gone, its price was known when it went."""
    price = model_catalog.price(model_id)
    if price is None:
        raise UnknownPrice(model_id)
    return (input_tokens * price[0] + output_tokens * price[1]) / PER_MILLION


def priced(entry: egress.Entry, price: Price) -> egress.Entry:
    """The attempt with the one price its call goes at, kept on its record until it
    settles."""
    return replace(entry, input_usd=price[0], output_usd=price[1])


def settled_cost(
    price: egress.Price, model_id: str, input_tokens: int, output_tokens: int
) -> float:
    """A sent call's cost at the price it was accepted at. A record from before that
    was kept is priced as today."""
    if price[0] is None or price[1] is None:
        return cost(model_id, input_tokens, output_tokens)
    return cost_at((price[0], price[1]), input_tokens, output_tokens)


def batched(model_id: str) -> bool:
    """Whether this model's provider has a batch API at half price."""
    return providers.of(model_id).batch


def page_read_estimate(price: Price | None = None) -> float:
    """What reading one page in the cloud costs at most, shown before it is asked: at
    the price given, or the page model's now."""
    if price is None:
        return cost(page_model(), PAGE_INPUT_TOKENS, PAGE_OUTPUT_TOKENS)
    return cost_at(price, PAGE_INPUT_TOKENS, PAGE_OUTPUT_TOKENS)


def page_model() -> str:
    """The one setting that names the model, the price and what's sent."""
    return str(settings.get(settings.MODEL_PAGE))


class CardEstimate(TypedDict):
    backfill_usd: float | None  # None: the helper model has no known price
    per_document_usd: float | None
    batched: bool
    model: str
    price: Price | None  # the one price read, that every amount here was worked out at


def card_estimate(
    excerpt_tokens: int, documents: int, backlog: int | None = None
) -> CardEstimate:
    """Writing cards with the helper model: once, then per new document. Only a backlog
    gets a batch price, only where the provider has one, and the backlog is everything
    waiting, not only these documents. None when the helper has no known price. The
    helper and its price are read once, and both are returned with the amounts."""
    model = str(settings.get(settings.MODEL_HELPER))
    price = price_of(model) if providers.is_model_id(model) else None
    if price is None:
        return {
            "backfill_usd": None,
            "per_document_usd": None,
            "batched": False,
            "model": model,
            "price": None,
        }
    is_batched = (
        batched(model) and (documents if backlog is None else backlog) > BATCH_ABOVE
    )
    whole = cost_at(
        price,
        excerpt_tokens + CARD_INSTRUCTION_TOKENS * documents,
        CARD_OUTPUT_TOKENS * documents,
    )
    each = cost_at(price, EXCERPT_TOKENS + CARD_INSTRUCTION_TOKENS, CARD_OUTPUT_TOKENS)
    return {
        "backfill_usd": round(whole * BATCH_SHARE if is_batched else whole, 4),
        "per_document_usd": round(each, 4),
        "batched": is_batched,
        "model": model,
        "price": price,
    }


def over_cap() -> bool:
    """Background cloud work stops here. Answers the owner asks for still run."""
    return spent_this_month() >= float(settings.get(settings.SPEND_CAP))


def reserve(entry: egress.Entry) -> int | None:
    """The cap's decision and the attempt's record, as one step."""
    return egress.reserve(entry, month_start(), float(settings.get(settings.SPEND_CAP)))


def month_start(today: datetime.date | None = None) -> float:
    day = (today or datetime.datetime.now(datetime.UTC).date()).replace(day=1)
    moment = datetime.datetime(day.year, day.month, 1, tzinfo=datetime.UTC)
    return moment.timestamp()


def spent_this_month() -> float:
    return egress.spent_since(month_start())
