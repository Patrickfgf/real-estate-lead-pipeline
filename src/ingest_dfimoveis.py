"""Ingestão da DFImóveis: rota FastAPI que recebe o webhook VrSync (push).

A DFImóveis (padrão VrSync do GrupoZAP/OLX) faz POST do lead na nossa URL.
Fluxo: valida o segredo (token na URL) → valida o payload (Pydantic) → normaliza
para o lead canônico → grava no DuckDB (dedup por originLeadId). Se a validação
falhar, guarda o cru na caixa de revisão (nada se perde).

Autenticação: o padrão VrSync não define um esquema; combinamos um token na
própria URL (?token=...) com a DFImóveis no cadastro — casa com
DFIMOVEIS_WEBHOOK_SECRET. Eles avaliam só o status HTTP (2xx = ok; re-tentam 3x
e guardam por até 14 dias em caso de falha).

Doc: https://developers.grupozap.com/webhooks/integration_leads.html
"""
import hmac
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from src.config import settings
from src.db import insert_dead_letter, insert_lead
from src.models import Lead, VrSyncLead
from src.transform import build_clean, dfimoveis_operation, safe_dfimoveis_listing_url
from src.trello import push_pending_leads

router = APIRouter(prefix="/webhook", tags=["ingestão"])
_TZ = ZoneInfo(settings.tz)
logger = logging.getLogger("jare.ingest")


def _check_secret(header_token: str | None, query_token: str | None) -> None:
    """Valida o segredo da DFImóveis. Aceita via header ou query (?token=).

    O VrSync não padroniza autenticação; combinamos um token na URL no cadastro.
    Sem segredo no .env, a validação é pulada (modo dev).
    """
    secret = settings.dfimoveis_webhook_secret
    if not secret:
        return
    # Comparação constant-time (hmac.compare_digest): evita um timing oracle que
    # deixaria adivinhar o segredo byte a byte pelo tempo de resposta do `==`.
    if not any(t is not None and hmac.compare_digest(secret, t) for t in (header_token, query_token)):
        raise HTTPException(status_code=401, detail="Segredo do webhook inválido")


def _parse_timestamp(value: str | None) -> datetime | None:
    """Converte o timestamp ISO 8601 do VrSync (com 'Z') em datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.post("/dfimoveis")
async def receber_lead_dfimoveis(
    request: Request,
    x_webhook_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    _check_secret(x_webhook_token, token)

    # Lê o corpo CRU primeiro: se o JSON nem parsear, ainda preservamos o texto.
    received_at = datetime.now(_TZ)
    raw_text = (await request.body()).decode("utf-8", errors="replace")

    try:
        payload = json.loads(raw_text)
        vrsync = VrSyncLead.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — JSON malformado OU validação Pydantic (intencional)
        await run_in_threadpool(insert_dead_letter, "dfimoveis", str(exc), raw_text, received_at)
        logger.warning("Lead DFImóveis recusado e guardado para revisão: %s", exc)
        raise HTTPException(
            status_code=422, detail=f"Payload inválido (guardado para revisão): {exc}"
        ) from exc

    # Fase 1: resolve o tipo de operação (Compra/Aluguel) ANTES de montar o Lead, para
    # gravá-lo no PRÓPRIO INSERT — fechando a janela em que um push concorrente veria o
    # lead com tipo NULL e cardaria no quadro fallback errado. Até 2026-08 os payloads
    # reais do DFImóveis NÃO traziam `transactionType`; hoje trazem (com o valor "SALE").
    # `dfimoveis_operation` prefere esse campo quando existe e cai numa heurística sobre
    # o `clientListingId` do CRM (aluguel) para os leads que não o tragam.
    # Best-effort: uma resolução que falhe não pode derrubar a ingestão (tipo fica None →
    # INSERT grava NULL).
    tipo = None
    try:
        tipo = dfimoveis_operation(payload)
    except Exception:  # noqa: BLE001 — enriquecimento; não derruba a ingestão
        logger.exception("Falha ao resolver transaction_type para %s", vrsync.origin_lead_id)

    lead = Lead(
        external_id=vrsync.origin_lead_id,
        source="dfimoveis",
        name=vrsync.name,
        email=vrsync.email,
        phone=vrsync.telefone_completo,
        message=vrsync.message,
        listing_ref=vrsync.client_listing_id or vrsync.origin_listing_id,
        lead_date=_parse_timestamp(vrsync.timestamp),
        raw_payload=json.dumps(payload, ensure_ascii=False),
        received_at=received_at,
        transaction_type=tipo,
        # Validação da URL na BORDA: um só ponto de confiança. O que não passa vira
        # None e o card cai no comportamento antigo (mostrar o código do anúncio). A
        # string original continua no raw_payload, então nada se perde.
        listing_url=safe_dfimoveis_listing_url(vrsync.listing_url),
    )

    inserted = await run_in_threadpool(insert_lead, lead)
    logger.info(
        "Lead %s/%s %s", lead.source, lead.external_id,
        "gravado (novo)" if inserted else "ignorado (duplicado)",
    )

    # Carga no Trello: best-effort e só quando há credenciais. Falha aqui não
    # derruba o webhook — o lead já está no banco e a próxima carga o reenvia.
    if inserted and settings.trello_api_key and settings.trello_list_id:
        try:
            await run_in_threadpool(push_pending_leads)
        except Exception:  # noqa: BLE001 — não derruba o webhook; fica pendente
            logger.exception("Falha na carga do Trello para %s", lead.external_id)

    # Mantém a camada curada (leads_clean) fresca para analytics/dashboard. Best-effort:
    # o lead já está na crua; falha aqui não derruba o webhook. No volume do projeto o
    # rebuild completo é barato; em escala, migrar para um rebuild agendado.
    if inserted:
        try:
            await run_in_threadpool(build_clean)
        except Exception:  # noqa: BLE001 — analytics; não derruba a ingestão
            logger.exception("Falha ao reconstruir leads_clean para %s", lead.external_id)

    return {
        "status": "received",
        "external_id": lead.external_id,
        "duplicate": not inserted,
    }
