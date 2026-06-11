"""App FastAPI do Projeto Jaré — ponto de entrada da ingestão de leads."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from src.admin import router as admin_router
from src.config import settings
from src.db import get_connection, ping
from src.ingest_dfimoveis import router as dfimoveis_router
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
    log = logging.getLogger("jare")
    # Fail-open visível: se algum webhook subir SEM segredo, ele aceita qualquer POST
    # (modo dev). Em produção isso é um buraco silencioso — então gritamos no boot.
    abertos = [
        nome
        for nome, segredo in (
            ("Wimóveis", settings.wimoveis_webhook_secret),
            ("DFImóveis", settings.dfimoveis_webhook_secret),
        )
        if not segredo
    ]
    if abertos:
        log.warning(
            "ATENÇÃO: webhook(s) %s SEM segredo — rodando em MODO ABERTO (sem "
            "autenticação). Defina o *_WEBHOOK_SECRET no .env antes de ir a produção.",
            ", ".join(abertos),
        )
    log.info("Projeto Jaré no ar — banco pronto.")
    yield


app = FastAPI(
    title="Projeto Jaré — Ingestão de Leads",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(wimoveis_router)
app.include_router(dfimoveis_router)
app.include_router(admin_router)


@app.get("/health", tags=["infra"])
def health(response: Response):
    """Deep check: prova que o DuckDB (onde toda ingestão grava) está vivo.

    Um monitor externo que recebe 200 com o banco quebrado reportaria 'saudável'
    enquanto 100% dos leads falham — por isso tocamos o banco de verdade.
    """
    try:
        ping()
    except Exception as exc:  # noqa: BLE001 — qualquer falha do banco = não-saudável
        logging.getLogger("jare").exception("Healthcheck falhou: banco inacessível")
        response.status_code = 503
        return {"status": "degraded", "db": f"erro: {exc}"}
    return {"status": "ok", "db": "ok"}
