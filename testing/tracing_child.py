"""A process for test_privacy_tracing.py: it runs a chain and a graph holding the
text it's given, after starting as SecureFolio's processes do when asked to, then
prints which LangSmith settings it was left with."""

import json
import os
import sys
from typing import TypedDict

from langchain_core.runnables import RunnableLambda
from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.graph import END, START, StateGraph

from backend import vault


class _State(TypedDict):
    text: str


def _answer(state: _State) -> _State:
    return {"text": f"{state['text']} answered"}


def main(text: str, start_as_securefolio: bool) -> None:
    if start_as_securefolio:
        vault.use_model_cache()
    RunnableLambda(lambda question: f"{question} answered").invoke(text)
    graph = StateGraph(_State)
    graph.add_node("answer", _answer)
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    graph.compile().invoke({"text": text})
    wait_for_all_tracers()
    print(json.dumps(sorted(name for name in os.environ if "LANGSMITH_API" in name)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] == "securefolio")
