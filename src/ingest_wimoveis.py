"""Ingestão do Wimóveis: rota FastAPI que recebe o callback (push) da Navent.

Fluxo: a Navent faz POST do evento CONTACTO → valida o segredo → valida o
payload (Pydantic) → normaliza para o lead canônico → grava no DuckDB
(dedup por idEvento).
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from src.config import settings
from src.db import insert_lead
from src.models import Lead, WimoveisContato
from src.trello import push_pending_leads

router = APIRouter(prefix="/webhook", tags=["ingestão"])
_TZ = ZoneInfo(settings.tz)


def _check_secret(header_token: str | None, query_token: str | None) -> None:
    """Valida o segredo compartilhado. Aceita via header ou query param.

    NOTA: confirmar com a doc oficial da Navent se/como o callback é autenticado
    (assinatura, token na URL etc.) e ajustar. Sem segredo configurado no .env,
    a validação é pulada (modo dev).
    """
    secret = settings.wimoveis_webhook_secret
    if not secret:
        return
    if secret not in (header_token, query_token):
        raise HTTPException(status_code=401, detail="Segredo do webhook inválido")


def _parse_data_registro(value: str | None) -> datetime | None:
    """Converte o dataRegistro da Navent (ISO 8601 com fuso) em datetime.

    Em caso de formato inesperado, retorna None — o valor original continua
    preservado no raw_payload, então não perdemos informação.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.post("/wimoveis")
async def receber_lead_wimoveis(
    request: Request,
    x_webhook_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    _check_secret(x_webhook_token, token)

    payload = await request.json()
    try:
        contato = WimoveisContato.model_validate(payload)
    except Exception as exc:  # validação Pydantic
        raise HTTPException(status_code=422, detail=f"Payload inválido: {exc}")

    lead = Lead(
        external_id=contato.id_evento,
        source="wimoveis",
        name=contato.nome,
        email=contato.email,
        phone=contato.telefone,
        message=contato.mensagem,
        listing_ref=contato.referencia,
        advertiser_code=contato.codigo_anunciante,
        agency_code=contato.codigo_imobiliaria,
        cpf=contato.cpf,
        lead_date=_parse_data_registro(contato.data_registro),
        raw_payload=json.dumps(payload, ensure_ascii=False),
        received_at=datetime.now(_TZ),
    )

    inserted = await run_in_threadpool(insert_lead, lead)

    # Carga no Trello: best-effort e só quando há credenciais. Falha aqui não
    # derruba o webhook — o lead já está no banco e a próxima carga o reenvia.
    if inserted and settings.trello_api_key and settings.trello_list_id:
        try:
            await run_in_threadpool(push_pending_leads)
        except Exception:  # noqa: BLE001
            pass

    return {
        "status": "received",
        "external_id": lead.external_id,
        "duplicate": not inserted,
    }
