"""What must never be imported or called from where (the module boundaries in
docs/ARCHITECTURE.md), across the backend, the worker, the agent and the evals."""

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PACKAGES = ("backend", "worker", "agent", "evals")
# A library, and the only modules allowed to import it.
ONLY_THROUGH = {
    "lancedb": {"backend/index.py"},
    "keyring": {"backend/keychain.py"},
    "ocrmac": {"worker/ocr.py"},
    "mlx": {"worker/page_reader.py"},
    "mlx_vlm": {"worker/page_reader.py"},
    "msoffcrypto": {"worker/unlock.py"},
    "anthropic": {"agent/models.py"},
    "langchain_anthropic": {"agent/models.py"},
    "langchain_ollama": {"agent/models.py"},
    "openai": {"agent/models.py"},
    "langchain_openai": {"agent/models.py"},
    "sentence_transformers": {"backend/embedder.py", "backend/reranker.py"},
}


def sources():
    for package in PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if "/tests/" not in relative and "/migrations/" not in relative:
                yield relative, ast.parse(path.read_text(encoding="utf-8"))


def imported(tree: ast.Module) -> set[str]:
    """Every module named by an import, including `from a import b` as a.b."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{alias.name}" for alias in node.names}
    return names


def test_each_library_is_reached_through_its_one_module():
    for relative, tree in sources():
        tops = {name.split(".")[0] for name in imported(tree)}
        for library, allowed in ONLY_THROUGH.items():
            if library in tops:
                assert relative in allowed, f"{relative} imports {library}"


def test_only_the_agent_service_imports_the_agent_from_the_backend():
    for relative, tree in sources():
        if relative.startswith("backend/") and relative != "backend/agent_service.py":
            assert not {n for n in imported(tree) if n.split(".")[0] == "agent"}, (
                relative
            )


def test_the_worker_imports_only_the_model_gateway_from_the_agent():
    for relative, tree in sources():
        if relative.startswith("worker/"):
            names = {n for n in imported(tree) if n.split(".")[0] == "agent"}
            assert names <= {"agent", "agent.models"}, relative


def test_the_agent_reaches_the_backend_only_through_its_api():
    for relative, tree in sources():
        if relative.startswith("agent/") and relative != "agent/api.py":
            names = {n for n in imported(tree) if n.split(".")[0] == "backend"}
            assert not names, relative


def test_nothing_outside_the_routes_and_main_imports_a_route():
    for relative, tree in sources():
        if not relative.startswith("backend/routes/") and relative != "backend/main.py":
            assert not {n for n in imported(tree) if n.startswith("backend.routes")}, (
                relative
            )


def test_only_the_gateway_builds_a_chat_model():
    for relative, tree in sources():
        if relative == "agent/models.py":
            continue
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "init_chat_model" not in calls, relative


def test_there_are_no_import_cycles():
    graph: dict[str, set[str]] = {}
    modules = {
        relative.removesuffix(".py").replace("/", ".") for relative, _ in sources()
    }
    for relative, tree in sources():
        name = relative.removesuffix(".py").replace("/", ".")
        graph[name] = {n for n in imported(tree) if n in modules}

    def visit(node: str, trail: tuple[str, ...]) -> None:
        assert node not in trail, f"cycle: {trail}"
        for next_node in graph.get(node, ()):
            visit(next_node, (*trail, node))

    for name in graph:
        visit(name, ())


PROVIDER_NAMES = ('"anthropic"', '"openai"', '"openrouter"')
KEY_ACCOUNTS = ("-api-key",)


def test_a_provider_is_named_only_in_providers():
    """Every other module asks `backend/providers.py`. A migration is a snapshot of its
    day, and keeps its own literals."""
    for relative, _tree in sources():
        if relative == "backend/providers.py":
            continue
        text = (ROOT / relative).read_text(encoding="utf-8")
        for name in PROVIDER_NAMES:
            assert name not in text, f"{relative} names the provider {name}"
        for account in KEY_ACCOUNTS:
            assert account not in text, f"{relative} names a Keychain account"


def test_who_answers_is_decided_in_one_place():
    """Only `policy.route` decides; the harness reads it through `api.py`."""
    for relative, tree in sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "route":
                # api.py only passes policy's decision on
                assert relative in ("backend/policy.py", "agent/api.py"), relative
