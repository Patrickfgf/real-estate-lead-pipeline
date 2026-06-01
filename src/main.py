"""App FastAPI do Projeto Jaré — ponto de entrada da ingestão de leads."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.db import get_connection
from src.ingest_wimoveis import router as wimoveis_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Garante que o banco e a tabela existam antes de aceitar requisições.
    get_connection()
    yield


app = FastAPI(
    title="Projeto Jaré — Ingestão de Leads",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(wimoveis_router)


@app.get("/health", tags=["infra"])
def health():
    return {"status": "ok"}
