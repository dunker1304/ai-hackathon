"""Ask layer: intent → deterministic tools → LLM narration, streamed as NDJSON.

Event lines:
    {"type": "tool", "name": "...", "args": {...}}
    {"type": "token", "content": "..."}
    {"type": "error", "message": "..."}
    {"type": "done"}
"""

from __future__ import annotations

import json
from typing import Iterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.orm import Session

from app.ask_tools import run_tool, tool_schemas
from app.llm import get_langfuse_handler, get_llm
from app.schemas import AskMessage

MAX_TOOL_ROUNDS = 4

ASK_SYSTEM = """You are the Product Opportunity Hub copilot for a print-on-demand R&D team.

Hard rules:
- Only cite numbers that appear in tool results. Never invent metrics.
- Mention the data source when quoting numbers (e.g. "per Etsy data (Alura export)").
- Product ids look like 'acrylic-ornament'. When the user names a product loosely, call rank_opportunities or normalize_listing first to find the right id.
- If no tool answers the question, say what data is missing — honesty beats guessing.
- ALWAYS end with a "**→ Recommendation**" block: what to make, which material, when to launch, and the top risk. This is mandatory for every answer.
- Keep answers compact: short paragraphs or bullet lists, no filler."""


def _to_lc_history(history: list[AskMessage]):
    out = []
    for m in history[-8:]:
        out.append(HumanMessage(m.content) if m.role == "user" else AIMessage(m.content))
    return out


def run_ask(db: Session, question: str, history: list[AskMessage]) -> Iterator[str]:
    def line(payload: dict) -> str:
        return json.dumps(payload) + "\n"

    handler = get_langfuse_handler()
    config = {"callbacks": [handler]} if handler else {}

    try:
        llm = get_llm().bind_tools(tool_schemas())
        messages = [SystemMessage(ASK_SYSTEM), *_to_lc_history(history), HumanMessage(question)]

        for _ in range(MAX_TOOL_ROUNDS):
            ai = llm.invoke(messages, config=config)
            if not ai.tool_calls:
                # Model answered directly without (further) tools.
                if ai.content:
                    yield line({"type": "token", "content": str(ai.content)})
                    yield line({"type": "done"})
                    return
                break
            messages.append(ai)
            for tc in ai.tool_calls:
                yield line({"type": "tool", "name": tc["name"], "args": tc["args"]})
                result = run_tool(db, tc["name"], tc["args"])
                messages.append(ToolMessage(json.dumps(result, default=str), tool_call_id=tc["id"]))

        # Final narration pass, streamed token by token (no tools bound).
        for event in get_llm().stream(messages, config=config):
            if event.content:
                yield line({"type": "token", "content": str(event.content)})
        yield line({"type": "done"})
    except Exception as exc:  # noqa: BLE001 - surface LLM/API failures to the UI
        yield line(
            {
                "type": "error",
                "message": (
                    "LLM unavailable: "
                    + str(exc)[:200]
                    + " — check OPENROUTER_API_KEY credits. Deterministic endpoints "
                    "(/opportunities, /normalize, /report) still work."
                ),
            }
        )
        yield line({"type": "done"})
