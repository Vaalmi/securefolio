"""`npm run verify:models`, on this Mac's own Ollama: the installed list, capabilities
and remote marks match what Ollama reports, and a figure is read with `local_figures`
set to a model that takes images. Needs Ollama running here; never part of
`npm run check`."""

import io

import httpx
import pytest
from PIL import Image, ImageDraw

from agent import models
from backend import agent_service, build_mode, model_store, settings

pytestmark = pytest.mark.real_models
OLLAMA = "http://127.0.0.1:11434"


@pytest.fixture
def ollama(library, monkeypatch):
    monkeypatch.delenv(build_mode.STUB_MODELS_ENV, raising=False)
    monkeypatch.setattr(agent_service, "OLLAMA_FRESH_SECONDS", 0.0)
    monkeypatch.setattr(model_store, "_capabilities", {})
    try:
        httpx.get(f"{OLLAMA}/api/version", timeout=2).raise_for_status()
    except httpx.HTTPError:
        pytest.fail("Start Ollama on this Mac first: verify:models reads its own list.")
    models.ollama_status()


def ollamas_own() -> dict[str, dict]:
    """Ollama's own answers, asked directly: its list, and each model's capabilities."""
    listed = httpx.get(f"{OLLAMA}/api/tags", timeout=10).json()["models"]
    found = {}
    for model in listed:
        remote = bool(model.get("remote_host") or model.get("remote_model"))
        shown = {}
        if not remote:
            shown = httpx.post(
                f"{OLLAMA}/api/show", json={"model": model["name"]}, timeout=30
            ).json()
        found[model["name"]] = {
            "size": model["size"],
            "remote": remote,
            "capabilities": shown.get("capabilities") or [],
        }
    return found


def test_the_list_and_what_each_can_do_match_ollama(ollama):
    own = ollamas_own()
    seen = model_store.installed_models()
    here = {model["name"]: model for model in seen["local"]}
    assert set(here) == {name for name, m in own.items() if not m["remote"]}
    assert {m["name"] for m in seen["remote"]} == {
        name for name, m in own.items() if m["remote"]
    }
    for name, model in here.items():
        can = own[name]["capabilities"]
        assert model["size"] == own[name]["size"], name
        assert model["text"] == ("completion" in can), name
        assert model["images"] == ("vision" in can), name
        print(
            f"{name}: text {model['text']}, images {model['images']}, {model['jobs']}"
        )


def a_bar_chart() -> bytes:
    """A small chart with its values written on it, as a page's figure would be."""
    image = Image.new("RGB", (480, 320), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 10), "Rent by month", fill="black")
    for at, (month, value) in enumerate((("Jan", 40), ("Feb", 60), ("Mar", 80))):
        left = 60 + at * 130
        draw.rectangle((left, 280 - value * 3, left + 80, 280), fill="steelblue")
        draw.text((left + 25, 290), month, fill="black")
        draw.text((left + 25, 260 - value * 3), str(value), fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_a_figure_is_read_by_a_model_that_takes_images(ollama):
    takes_images = [
        m
        for m in model_store.installed_models()["local"]
        if m["images"] and settings.LOCAL_FIGURES in m["jobs"]
    ]
    assert takes_images, "no model here takes images"
    smallest = min(takes_images, key=lambda m: m["size"])["name"]
    settings.put(settings.LOCAL_FIGURES, smallest)
    reading = models.read_picture(
        a_bar_chart(), models.local_tag(settings.LOCAL_FIGURES)
    )
    print(f"{smallest} read: {reading}")
    assert reading.get("kind") in ("chart", "figure")
    said = str(reading).casefold()
    assert "jan" in said or "rent" in said, "it read the picture, not a guess"
