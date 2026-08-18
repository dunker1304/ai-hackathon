from pydantic import BaseModel


class IngestRequest(BaseModel):
    title: str
    text: str
    source: str | None = None


class IngestResponse(BaseModel):
    document_id: int
    chunks_created: int


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
