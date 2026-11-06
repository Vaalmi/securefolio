"""Does langchain-anthropic pass search_result blocks with per-element text blocks,
and return their citations? One small live call. Run with .env's key:
uv run --env-file .env -m scripts.measure.citations_live"""

import json
import os

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from backend import telemetry

telemetry.turn_off()
headers = {"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]}
model = init_chat_model(
    "anthropic:claude-sonnet-5-5", max_tokens=200, default_headers=headers
)
message = HumanMessage(
    content=[
        {
            "type": "search_result",
            "source": "securefolio://doc/d1/rev/r1#chunk=c1",
            "title": "Warranty booklet, page 7",
            "content": [
                {
                    "type": "text",
                    "text": "Basic coverage lasts 3 years or 36,000 miles.",
                },
                {"type": "text", "text": "The in-service date is March 15, 2024."},
            ],
            "citations": {"enabled": True},
        },
        {"type": "text", "text": "When does basic coverage end? One sentence."},
    ]
)
answer = model.invoke([message])
print(json.dumps(answer.content, indent=1, default=str))
print(json.dumps(answer.usage_metadata))
