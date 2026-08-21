from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import chat, crawl, documents, hub

app = FastAPI(title="Product Opportunity Hub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(hub.router)
app.include_router(crawl.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
