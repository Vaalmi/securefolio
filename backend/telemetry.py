"""Third-party telemetry and tracing, off in every SecureFolio process. LangChain and
LangGraph trace each run to LangSmith's servers whenever the environment asks, which
would carry Private prompts and answers off the Mac outside agent/models.py's gate
and the egress log. So whatever the process inherited, nothing traces."""

import os

import langsmith

_TRACING_PREFIXES = ("LANGSMITH_", "LANGCHAIN_")


def turn_off() -> None:
    """Drops every LangSmith and LangChain setting the process inherited, and turns
    tracing off, before any model or graph runs."""
    for name in [name for name in os.environ if name.startswith(_TRACING_PREFIXES)]:
        del os.environ[name]
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    # Overrides whatever LangSmith already read from the environment.
    langsmith.configure(enabled=False)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
