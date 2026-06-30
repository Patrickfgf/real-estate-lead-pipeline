"""Ingestão do Wimóveis: rota FastAPI que recebe o callback (push) da Navent.

Fluxo: a Navent faz POST do evento CONTACTO → valida o segredo → valida o
payload (Pydantic) → normaliza para o lead canônico → grava no DuckDB
(dedup por idEvento).
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
from src.models import Lead, WimoveisContato
from src.transform import build_clean, clean_message
from src.trello import push_pending_leads

router = APIRouter(prefix="/webhook", tags=["ingestão"])
_TZ = ZoneInfo(settings.tz)
logger = logging.getLogger("jare.ingest")


def _check_secret(header_token: str | None, query_token: str | None) -> None:
    """Valida o segredo compartilhado. Aceita via header ou query param.

    Como a Navent autentica (doc open.navent.com/guias/callbacks): ao cadastrar
    o callback (PUT /v1/configuracion/callbacks ou via suporte), NÓS definimos
    `authorizationHeaderKey` (nome do header; default "Authorization") e
    `authorizationHeaderValue` (o segredo). A Navent envia esse header em todo
    POST. Para casar com a checagem abaixo SEM mudar código, cadastrar com
    authorizationHeaderKey="x-webhook-token" e authorizationHeaderValue igual ao
    WIMOVEIS_WEBHOOK_SECRET. (Se preferir o header padrão "Authorization", ler
    `authorization` aqui.) Sem segredo no .env, a validação é pulada (modo dev).
    """
    secret = settings.wimoveis_webhook_secret
    if not secret:
        return
    # Comparação constant-time (hmac.compare_digest): evita um timing oracle que
    # deixaria adivinhar o segredo byte a byte pelo tempo de resposta do `==`.
    if not any(t is not None and hmac.compare_digest(secret, t) for t in (header_token, query_token)):
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

    # Lê o corpo CRU primeiro: se o JSON nem parsear, ainda preservamos o texto.
    received_at = datetime.now(_TZ)
    raw_text = (await request.body()).decode("utf-8", errors="replace")

    try:
        payload = json.loads(raw_text)
        contato = WimoveisContato.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — JSON malformado OU validação Pydantic (intencional)
        # Rede de segurança: guarda o cru na caixa de revisão antes de recusar.
        # Nenhum lead se perde, mesmo vindo num formato inesperado.
        await run_in_threadpool(insert_dead_letter, "wimoveis", str(exc), raw_text, received_at)
        logger.warning("Lead Wimóveis recusado e guardado para revisão: %s", exc)
        raise HTTPException(
            status_code=422, detail=f"Payload inválido (guardado para revisão): {exc}"
        ) from exc

    # idnavplat (ID do aviso na Navent) é o vínculo com o imóvel que SEMPRE vem; a
    # 'referencia' (código legível do CRM) só chega se a corretora associou o aviso,
    # então tem precedência quando existe.
    listing_ref = contato.referencia or (
        str(contato.id_navplat) if contato.id_navplat is not None else None
    )

    lead = Lead(
        external_id=contato.id_evento,
        source="wimoveis",
        name=contato.nome,
        email=contato.email,
        phone=contato.telefone,
        message=clean_message(contato.mensagem),
        listing_ref=listing_ref,
        advertiser_code=contato.codigo_anunciante,
        agency_code=contato.codigo_imobiliaria,
        cpf=contato.cpf,
        lead_date=_parse_data_registro(contato.data_registro),
        raw_payload=json.dumps(payload, ensure_ascii=False),
        received_at=received_at,
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
