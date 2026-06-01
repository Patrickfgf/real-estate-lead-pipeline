"""Ingestão do Wimóveis: rota FastAPI que recebe o webhook oficial.

Fluxo: POST chega → valida o segredo → valida o payload (Pydantic) →
normaliza para o lead canônico → grava no DuckDB (dedup por external_id).
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from src.config import settings
from src.db import insert_lead
from src.models import Lead, WimoveisLead

router = APIRouter(prefix="/webhook", tags=["ingestão"])
_TZ = ZoneInfo(settings.tz)


def _check_secret(header_token: str | None, query_token: str | None) -> None:
    """Valida o segredo compartilhado. Aceita via header ou query param.

    NOTA: confirmar com a doc oficial do Wimóveis como o segredo deve ser
    enviado (header vs. token na URL) e ajustar se necessário. Sem segredo
    configurado no .env, a validação é pulada (modo dev).
    """
    secret = settings.wimoveis_webhook_secret
    if not secret:
        return
    if secret not in (header_token, query_token):
        raise HTTPException(status_code=401, detail="Segredo do webhook inválido")


@router.post("/wimoveis")
async def receber_lead_wimoveis(
    request: Request,
    x_webhook_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    _check_secret(x_webhook_token, token)

    payload = await request.json()
    try:
        raw = WimoveisLead.model_validate(payload)
    except Exception as exc:  # validação Pydantic
        raise HTTPException(status_code=422, detail=f"Payload inválido: {exc}")

    lead = Lead(
        external_id=raw.external_id,
        source="wimoveis",
        name=raw.name,
        email=raw.email,
        phone=raw.phone,
        message=raw.message,
        business_type=raw.business_type,
        broker_email=raw.broker_email,
        origin=raw.origin,
        raw_payload=json.dumps(payload, ensure_ascii=False),
        received_at=datetime.now(_TZ),
    )

    inserted = await run_in_threadpool(insert_lead, lead)
    return {
        "status": "received",
        "external_id": lead.external_id,
        "duplicate": not inserted,
    }
