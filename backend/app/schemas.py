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


# --- Product Opportunity Hub ---


class ProductTypeOut(BaseModel):
    id: str
    name: str
    category: str
    material: str
    difficulty: int
    margin_min: float
    margin_max: float
    personalization_friendly: bool
    fit: int
    seasonality: dict

    model_config = {"from_attributes": True}


class OpportunityOut(BaseModel):
    product_type_id: str
    name: str
    category: str
    material: str
    total: float
    fit: int
    decision: str
    dims: dict
    computed_at: str


class OpportunitiesResponse(BaseModel):
    items: list[OpportunityOut]
    default_weights: dict[str, float]


class NormalizeRequest(BaseModel):
    title: str


class CandidateOut(BaseModel):
    id: str
    name: str
    similarity: float


class NormalizeResponse(BaseModel):
    status: str  # "matched" | "out_of_catalog"
    product_type: ProductTypeOut | None
    confidence: float
    reasoning: str
    evidence_span: str
    candidates: list[CandidateOut]
    signature: dict


class AskMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class AskRequest(BaseModel):
    question: str
    history: list[AskMessage] = []


class FreshnessSource(BaseModel):
    source: str
    fetched_at: str
    count: int


class FreshnessResponse(BaseModel):
    sources: list[FreshnessSource]
    scores_computed_at: str | None


class ReportResponse(BaseModel):
    product_type_id: str
    markdown: str
