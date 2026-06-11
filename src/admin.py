"""Operação via HTTP: endpoints administrativos que rodam DENTRO do processo da API.

O DuckDB é single-writer: o uvicorn segura o lock exclusivo do arquivo enquanto
está no ar, então qualquer `python -m src.db`/`src.trello` num SEGUNDO processo
(ex.: via `fly ssh console`) falha com "Conflicting lock is held". A operação do
dia a dia (inspecionar a caixa de revisão, reenviar pendentes ao Trello) precisa
acontecer no processo que já detém a conexão — estes endpoints fazem isso.

Autenticação: fail-closed. Sem ADMIN_TOKEN configurado, os endpoints respondem
503 — ao contrário dos webhooks (fail-open em dev), um painel de operação aberto
não tem modo "inofensivo".
"""
import hmac

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool

from src.config import settings
from src.db import fetch_dead_letter
from src.trello import push_pending_leads

router = APIRouter(prefix="/admin", tags=["operação"])


def _check_token(token: str | None) -> None:
    if not settings.admin_token:
        raise HTTPException(
            status_code=503, detail="ADMIN_TOKEN não configurado — endpoints admin desabilitados"
        )
    if token is None or not hmac.compare_digest(settings.admin_token, token):
        raise HTTPException(status_code=401, detail="Token admin inválido")


@router.get("/dead-letter")
async def dead_letter(
    x_admin_token: str | None = Header(default=None),
    limit: int = 20,
):
    """Caixa de revisão: POSTs que não passaram na validação (mais recentes primeiro)."""
    _check_token(x_admin_token)
    entradas = await run_in_threadpool(fetch_dead_letter, limit)
    return {"total": len(entradas), "entradas": entradas}


@router.post("/trello/push")
async def trello_push(x_admin_token: str | None = Header(default=None)):
    """Reenvia ao Trello os leads pendentes (trello_card_id IS NULL)."""
    _check_token(x_admin_token)
    if not (settings.trello_api_key and settings.trello_list_id):
        raise HTTPException(status_code=503, detail="Credenciais do Trello não configuradas")
    return await run_in_threadpool(push_pending_leads)
