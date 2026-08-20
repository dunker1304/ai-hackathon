from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.llm import get_langfuse_handler, get_llm
from app.rag import retrieve
from app.schemas import ChatRequest
from database.db import get_db

router = APIRouter(prefix="/chat", tags=["chat"])

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's question using ONLY the "
    "context provided below. If the context doesn't contain the answer, say so "
    "plainly instead of guessing.\n\nContext:\n{context}"
)


@router.post("")
async def chat(payload: ChatRequest, db: Annotated[Session, Depends(get_db)]) -> StreamingResponse:
    chunks = await retrieve(db, payload.question, top_k=payload.top_k)
    context = "\n\n---\n\n".join(c.content for c in chunks) or "No relevant context found."

    messages = [
        SystemMessage(content=SYSTEM_PROMPT.format(context=context)),
        HumanMessage(content=payload.question),
    ]

    llm = get_llm()
    handler = get_langfuse_handler()
    config = {"callbacks": [handler]} if handler else {}

    def token_stream() -> Iterable[str | list[Any]]:
        for event in llm.stream(messages, config=config):
            if event.content:
                yield event.content

    return StreamingResponse(token_stream(), media_type="text/plain")
