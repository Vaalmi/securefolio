"""Who answers a question, case by case, for the answer and for the helpers."""

import pytest

from backend import (
    connectivity,
    keychain,
    model_catalog,
    model_store,
    policy,
    providers,
    settings,
)
from testing.stub_models import OPENROUTER_LIST

SONNET = "anthropic:claude-sonnet-5-5"
OPUS = "anthropic:claude-opus-5-5"
HAIKU = "anthropic:claude-haiku-5-5"
SOL = "openai:gpt-6.1-sol"
LUNA = "openai:gpt-6-luna"
GEMMA = "gemma4:26b"


@pytest.fixture
def world(library, monkeypatch):
    """Both cloud providers with keys and a connection, and Ollama holding gemma."""
    reachable: dict[str, bool] = {p: True for p in providers.PROVIDERS}
    monkeypatch.setattr(
        connectivity,
        "reachable",
        lambda p=providers.ANTHROPIC, _wait=True: reachable[p],
    )
    model_store.remember_ollama([{"name": GEMMA}])
    for provider in providers.PROVIDERS:
        keychain.set_key(f"test-key-{provider}", provider)
    yield reachable
    for provider in providers.PROVIDERS:
        keychain.delete_key(provider)


def blocked_of(made: policy.Route) -> str:
    assert made.blocked is not None
    return made.blocked


def choose(answer=SONNET, deep=OPUS, helper=HAIKU) -> None:
    settings.put(settings.MODEL_ANSWER, answer)
    settings.put(settings.MODEL_DEEP, deep)
    settings.put(settings.MODEL_HELPER, helper)


class TestTheAnswer:
    def test_offline_answers_locally_and_says_so(self, world):
        settings.put(settings.OFFLINE, True)
        made = policy.route("standard")
        assert (made.local, made.model, made.reason) == (True, GEMMA, "Offline")
        assert made.helper_local
        assert made.blocked is None

    @pytest.mark.parametrize(
        "how",
        [{"mode": "private"}, {"include_private": True}, {"thread_private": True}],
    )
    def test_private_answers_locally_with_no_reason(self, world, how):
        made = policy.route(how.pop("mode", "standard"), **how)
        assert (made.local, made.reason, made.blocked) == (True, None, None)
        assert made.helper_local

    def test_an_unpriced_model_blocks_and_nothing_switches_on_its_own(self, world):
        choose(answer="openrouter:someone/unlisted")
        made = policy.route("standard")
        assert made.local is False
        assert "no known price" in blocked_of(made).lower()
        assert "Refresh OpenRouter's list" in blocked_of(made)

    def test_a_removed_model_blocks_like_an_unpriced_one(self, world):
        choose(answer="anthropic:claude-retired-1")
        assert "no known price" in blocked_of(policy.route("standard")).lower()

    def test_a_removed_deep_model_blocks_deep_only(self, world):
        choose(deep="openai:gpt-retired")
        assert policy.route("deep").blocked
        assert policy.route("standard").blocked is None
        assert policy.route("standard", preview_deep=True).deep_cloud is False

    def test_no_key_answers_locally_and_names_the_key(self, world):
        choose(answer=SOL)
        keychain.delete_key(providers.OPENAI)
        made = policy.route("standard")
        assert (made.local, made.model) == (True, GEMMA)
        assert made.reason == (
            "GPT-6.1 Sol needs an OpenAI key (or its Project ID). Add it in Settings, "
            "or choose another model."
        )

    def test_a_key_that_needs_its_id_isnt_ready(self, world):
        choose(answer=SOL)
        settings.put(settings.OPENAI_NEEDS_ID, True)
        assert policy.route("standard").local is True
        settings.put(settings.OPENAI_NEEDS_ID, False)
        assert policy.route("standard").local is False

    def test_no_connection_answers_locally(self, world):
        choose(answer=SOL)
        world[providers.OPENAI] = False
        made = policy.route("standard")
        assert made.local
        assert made.reason == "No connection to OpenAI"

    def test_a_cut_provider_falls_back_only_for_its_own_tasks(self, world):
        world[providers.OPENAI] = False
        choose(answer=SONNET, helper=LUNA)
        made = policy.route("standard")
        assert (made.local, made.model, made.helper_local) == (False, SONNET, True)

    def test_otherwise_the_chosen_model_answers(self, world):
        choose(answer=SOL, deep=OPUS)
        made = policy.route("standard")
        assert (made.local, made.model, made.name, made.provider) == (
            False,
            SOL,
            "GPT-6.1 Sol",
            "openai",
        )
        deep = policy.route("deep", preview_deep=True)
        assert (deep.model, deep.provider, deep.deep_cloud) == (OPUS, "anthropic", True)

    @pytest.mark.parametrize("case", ["offline", "private", "no key", "no connection"])
    def test_answering_locally_with_the_tag_gone_blocks(self, world, case):
        model_store.remember_ollama([{"name": "llama9:8b"}])
        choose(answer=SOL)
        if case == "offline":
            settings.put(settings.OFFLINE, True)
        if case == "no key":
            keychain.delete_key(providers.OPENAI)
        if case == "no connection":
            world[providers.OPENAI] = False
        made = policy.route("private" if case == "private" else "standard")
        assert made.local
        assert (
            made.blocked
            == f"{GEMMA} is no longer in Ollama. Choose another in Settings."
        )

    def test_a_tag_on_ollamas_servers_isnt_installed(self, world):
        model_store.remember_ollama([{"name": GEMMA, "remote": True}])
        settings.put(settings.OFFLINE, True)
        assert policy.route("standard").blocked

    def test_before_ollama_has_been_asked_nothing_is_blocked_for_it(
        self, world, monkeypatch
    ):
        monkeypatch.setattr(model_store, "_ollama", {"at": None, "models": []})
        settings.put(settings.OFFLINE, True)
        assert policy.route("standard").blocked is None


class TestTheHelpers:
    def test_on_another_provider_with_its_key(self, world):
        choose(answer=SONNET, helper=LUNA)
        made = policy.route("standard")
        assert (made.local, made.helper_local, made.helper_model) == (
            False,
            False,
            LUNA,
        )

    @pytest.mark.parametrize("missing", ["key", "connection"])
    def test_a_helper_without_its_key_or_connection_runs_locally(self, world, missing):
        choose(answer=SONNET, helper=LUNA)
        if missing == "key":
            keychain.delete_key(providers.OPENAI)
        else:
            world[providers.OPENAI] = False
        made = policy.route("standard")
        assert (made.local, made.helper_local, made.blocked) == (False, True, None)

    def test_a_helper_running_locally_with_no_local_model_blocks(self, world):
        model_store.remember_ollama([])
        choose(answer=SONNET, helper=LUNA)
        keychain.delete_key(providers.OPENAI)
        made = policy.route("standard")
        assert made.blocked == (
            "The helper model needs OpenAI's key, and no local model is installed."
        )

    def test_an_unpriced_helper_blocks(self, world):
        choose(answer=SONNET, helper="openrouter:gone/model")
        assert "no known price" in blocked_of(policy.route("standard")).lower()

    @pytest.mark.parametrize("how", ["offline", "private"])
    def test_a_private_or_offline_answer_runs_its_helpers_locally(self, world, how):
        choose(answer=SONNET, helper=LUNA)
        if how == "offline":
            settings.put(settings.OFFLINE, True)
        made = policy.route("private" if how == "private" else "standard")
        assert made.helper_local
        assert made.helper_model == GEMMA

    def test_the_answer_and_the_helper_each_miss_their_key_in_turn(self, world):
        choose(answer=SONNET, helper=LUNA)
        keychain.delete_key(providers.ANTHROPIC)
        made = policy.route("standard")
        assert (made.local, made.helper_local) == (True, False)
        keychain.set_key("test-key-anthropic", providers.ANTHROPIC)
        keychain.delete_key(providers.OPENAI)
        made = policy.route("standard")
        assert (made.local, made.helper_local) == (False, True)


class TestDeep:
    def test_a_deep_model_that_would_fall_back_isnt_deep(self, world):
        choose(deep=SOL)
        keychain.delete_key(providers.OPENAI)
        assert policy.route("standard", preview_deep=True).deep_cloud is False
        assert policy.route("deep").local is True


class TestTheOpenRouterList:
    @pytest.fixture
    def listed(self, world):
        rows = [r for r in map(model_catalog.to_row, OPENROUTER_LIST) if r]
        model_catalog.store_openrouter(rows)
        return rows

    def test_a_listed_and_priced_model_answers(self, listed):
        choose(answer="openrouter:openai/gpt-oss-120b")
        made = policy.route("standard")
        assert (made.local, made.provider) == (False, "openrouter")

    def test_a_model_priced_per_request_has_no_known_price(self, listed):
        choose(answer="openrouter:openrouter/auto")
        assert "no known price" in blocked_of(policy.route("standard")).lower()
