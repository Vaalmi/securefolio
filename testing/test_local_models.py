"""A local model for each job: what Ollama has installed and what each model can do,
each job's checks, and the memory the chosen models need."""

import json
from pathlib import Path

import pytest

from backend import agent_service, model_store, settings
from backend.build_mode import STUB_OLLAMA_ENV
from testing.stub_models import stub_ollama

RECORDED = json.loads((Path(__file__).parent / "fixtures/ollama_0_40.json").read_text())
# Recorded on this Mac's Ollama 0.40.14, with two models it hasn't added by hand: a
# text-only one and one on Ollama's servers.
TAGS = {"models": [*RECORDED["tags"]["models"], *RECORDED["by_hand"]["tags"]]}
SHOW = {**RECORDED["show"], **RECORDED["by_hand"]["show"]}
GEMMA = "gemma4:26b"  # takes images: the default for every job
SMALL = "gemma4:e2b-it-qat"  # takes images; the one loaded when /api/ps was recorded
QWEN = "llama3.2:3b"  # text only (by hand: every text model on this Mac takes images)
EMBED = "nomic-embed-text:latest"  # embedding only
REMOTE = "gpt-oss:120b-cloud"  # on Ollama's servers (by hand)


def recorded(path: str, body: dict) -> dict:
    if path == "/api/tags":
        return TAGS
    if path == "/api/show":
        return SHOW[body["model"]]
    if path == "/api/ps":
        return RECORDED["ps"]
    return {"model": body.get("model"), "message": {"content": "x"}, "done": True}


@pytest.fixture
def ollama(library, monkeypatch):
    """This Mac's Ollama, answering as Ollama 0.40 does, freshly listed."""
    monkeypatch.setattr(agent_service, "OLLAMA_FRESH_SECONDS", 0.0)
    monkeypatch.setattr(model_store, "_capabilities", {}, raising=False)
    with stub_ollama() as stub:
        stub.reply = recorded
        monkeypatch.setenv(STUB_OLLAMA_ENV, stub.url)
        yield stub


def models_of(app_client) -> dict:
    return app_client.get("/api/models").json()


class TestTheInstalledList:
    def test_each_model_with_its_size_and_what_it_can_do(self, ollama, app_client):
        found = models_of(app_client)["installed"]
        here = {model["name"]: model for model in found["local"]}
        recorded_here = {m["name"] for m in RECORDED["tags"]["models"]}
        assert set(here) == recorded_here | {QWEN}
        assert here[GEMMA]["size"] == 16146867483
        assert (here[GEMMA]["text"], here[GEMMA]["images"]) == (True, True)
        assert (here[QWEN]["text"], here[QWEN]["images"]) == (True, False)
        assert (here[EMBED]["text"], here[EMBED]["images"]) == (False, False)
        assert here[GEMMA]["memory"] == {"gb": 15.0, "about": True}, (
            "its size, till loaded"
        )

    def test_an_embedding_only_model_is_listed_but_offered_for_no_job(
        self, ollama, app_client
    ):
        here = {m["name"]: m for m in models_of(app_client)["installed"]["local"]}
        assert here[EMBED]["jobs"] == []
        assert here[QWEN]["jobs"] == [settings.LOCAL_ANSWERS, settings.LOCAL_CARDS]
        assert here[GEMMA]["jobs"] == list(settings.LOCAL_JOBS)

    def test_a_model_on_ollamas_servers_is_listed_apart(self, ollama, app_client):
        found = models_of(app_client)["installed"]
        assert found["remote"] == [{"name": REMOTE}]
        assert REMOTE not in {model["name"] for model in found["local"]}

    def test_capabilities_are_asked_once_per_model(self, ollama, app_client):
        models_of(app_client)
        models_of(app_client)
        shown = [r.body["model"] for r in ollama.requests if r.path == "/api/show"]
        local = [m["name"] for m in TAGS["models"] if m["name"] != REMOTE]
        assert sorted(shown) == sorted(local), "once each, and never the cloud one"


class TestEachJobsChecks:
    @pytest.mark.parametrize(
        ("job", "tag", "says"),
        [
            ("local_figures", QWEN, "doesn't take images"),
            ("local_answers", EMBED, "doesn't generate text"),
            ("local_cards", EMBED, "doesn't generate text"),
            ("local_answers", "llama9:8b", "isn't installed"),
            ("local_answers", REMOTE, "Ollama's servers"),
            (
                "local_figures",
                "anthropic:claude-haiku-5-5",
                "takes a model from Ollama",
            ),
        ],
    )
    def test_a_tag_that_breaks_a_jobs_needs_is_refused(
        self, ollama, app_client, job, tag, says
    ):
        refused = app_client.patch("/api/settings", json={job: tag})
        assert refused.status_code == 422
        assert says in refused.json()["detail"]
        assert settings.get(job) != tag

    @pytest.mark.parametrize(
        ("job", "tag"),
        [("local_answers", QWEN), ("local_cards", QWEN), ("local_figures", GEMMA)],
    )
    def test_a_tag_that_suits_the_job_is_kept(self, ollama, app_client, job, tag):
        assert app_client.patch("/api/settings", json={job: tag}).status_code == 200
        assert settings.get(job) == tag
        jobs = models_of(app_client)["jobs"]
        assert jobs[job] == {"tag": tag, "problem": None}

    def test_each_job_reads_its_own_tag_and_local_model_overrides_all_three(
        self, ollama, app_client, monkeypatch
    ):
        settings.put(settings.LOCAL_CARDS, QWEN)
        jobs = models_of(app_client)["jobs"]
        assert jobs["local_cards"]["tag"] == QWEN
        assert jobs["local_answers"]["tag"] == GEMMA
        monkeypatch.setenv("LOCAL_MODEL", QWEN)
        jobs = models_of(app_client)["jobs"]
        assert {job["tag"] for job in jobs.values()} == {QWEN}


class TestTheMemory:
    def test_a_loaded_model_shows_ollamas_own_figure(self, ollama, app_client):
        for job in settings.LOCAL_JOBS:
            settings.put(job, SMALL)
        memory = models_of(app_client)["memory"]
        assert memory == {"gb": 0.4, "about": False}  # Ollama's /api/ps figure

    def test_one_not_yet_loaded_is_estimated_from_its_size_and_marked_about(
        self, ollama, app_client
    ):
        settings.put(settings.LOCAL_ANSWERS, SMALL)
        settings.put(settings.LOCAL_CARDS, SMALL)
        memory = models_of(app_client)["memory"]  # figures still on gemma4:26b
        assert memory == {"gb": 15.4, "about": True}  # 0.4 measured + 15.0 on disk

    def test_a_figure_is_kept_by_tag_after_the_model_unloads(self, ollama, app_client):
        for job in settings.LOCAL_JOBS:
            settings.put(job, SMALL)
        models_of(app_client)
        ollama.reply = lambda path, body: (
            {"models": []} if path == "/api/ps" else recorded(path, body)
        )
        assert models_of(app_client)["memory"] == {"gb": 0.4, "about": False}

    def test_each_installed_model_has_its_own_and_this_mac_has_its_total(
        self, ollama, app_client, monkeypatch
    ):
        monkeypatch.setattr(model_store, "memory_gib", lambda: 48)
        found = models_of(app_client)
        here = {model["name"]: model for model in found["installed"]["local"]}
        assert here[SMALL]["memory"] == {"gb": 0.4, "about": False}
        assert found["mac_memory_gb"] == 48

    @pytest.mark.parametrize(
        ("memory", "tag"), [(64, GEMMA), (32, "gemma4:12b"), (16, "qwen3.5:9b")]
    )
    def test_the_suggested_model_follows_the_memory_table(self, memory, tag):
        assert model_store.default_local_tag(memory) == tag


class TestNothingOnAGuess:
    """A model is accepted for a job only on what Ollama says it can do."""

    def test_a_model_ollama_hasnt_described_is_refused(self, ollama, app_client):
        ollama.reply = lambda path, body: (
            {"error": "timed out"} if path == "/api/show" else recorded(path, body)
        )
        refused = app_client.patch("/api/settings", json={"local_figures": QWEN})
        assert refused.status_code == 422
        assert "hasn't said what" in refused.json()["detail"]

    def test_a_new_version_of_a_tag_is_asked_about_again(self, ollama, app_client):
        models_of(app_client)  # gemma4:26b described, as it was
        listed = json.loads(json.dumps(TAGS))
        gemma = next(m for m in listed["models"] if m["name"] == GEMMA)
        gemma["digest"] = "0" * 64  # pulled again: a new version
        ollama.reply = lambda path, body: (
            listed
            if path == "/api/tags"
            else {"error": "timed out"}
            if path == "/api/show"
            else recorded(path, body)
        )
        refused = app_client.patch("/api/settings", json={"local_figures": GEMMA})
        assert refused.status_code == 422, "the old version's answer doesn't count"
