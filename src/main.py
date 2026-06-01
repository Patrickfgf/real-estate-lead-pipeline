"""App FastAPI do Projeto Jaré — ponto de entrada da ingestão de leads."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.db import get_connection
from src.ingest_wimoveis import router as wimoveis_router

# Registro de eventos: dá visibilidade em produção (lead recebido, dup, falha no
# Trello, payload guardado para revisão). basicConfig só age se ninguém mais
# configurou logging, então não conflita com o handler do uvicorn.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Garante que o banco e a tabela existam antes de aceitar requisições.
    get_connection()
    logging.getLogger("jare").info("Projeto Jaré no ar — banco pronto.")
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
