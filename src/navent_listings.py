"""Resolve o tipo de operação (Compra/Aluguel) de um anúncio via Navent Open API.

O callback CONTACTO do Wimóveis NÃO traz o tipo de transação (diferente do VrSync
da DFImóveis, que manda `transactionType`). Para separar os leads em 2 quadros do
Trello (Fase 1), consultamos a Navent a partir do `idnavplat` (ID do aviso) e do
`codigoImobiliaria` do lead.

Estratégia (1 GET só) — validada empiricamente com token de produção em 4 anúncios:
    GET .../anuncios/online/resumo?idAvisoNavplat=<id>  -> lê o `titulo` do 1º item.
O `titulo` indica a operação de forma inequívoca ("... à venda ...", "... para
aluguel ..."), então parseamos ele. A via antiga (resumo -> `codigoAviso` -> GET
do anúncio -> `precios[].operacion`) está FECHADA: o `codigoAviso` vem null e o GET
do anúncio devolve HTTP 500 "Access is denied" (o token não tem permissão).

BEST-EFFORT: qualquer exceção/timeout/HTTP de erro/formato inesperado/credencial
ausente devolve None e NUNCA levanta — a resolução do tipo é um enriquecimento
opcional e não pode derrubar o webhook nem bloquear a gravação do lead.

Autentica com o NAVENT_TOKEN (o mesmo de src/navent.py: "Authorization: Bearer …").
"""
import logging
import unicodedata
from urllib.parse import quote

import requests

from src.config import settings

logger = logging.getLogger("jare.navent")

_TIMEOUT = 4  # timeout curto: enriquecimento best-effort; não vale esperar 8s por GET.

# Termos que decidem a operação a partir do `titulo` (já normalizado: lower + sem
# acento). Casamos por substring: se casar SÓ um lado → tipo; ambos ou nenhum → None.
_TERMOS_ALUGUEL = ("aluguel", "aluga", "alugar", "locacao", "para alugar", "temporada")
_TERMOS_COMPRA = ("venda", "vende", "a venda")


def _normalize(value: str) -> str:
    """lower() + remoção de acentos (NFKD) — 'à venda'/'locação' viram 'a venda'/'locacao'."""
    decomposed = unicodedata.normalize("NFKD", value)
    sem_acento = "".join(c for c in decomposed if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def _operacao_do_titulo(titulo: str | None) -> str | None:
    """Deriva "Compra" | "Aluguel" | None a partir do `titulo` do anúncio (função pura).

    O resumo não traz o tipo em campo próprio, mas o título é inequívoco. Normaliza
    (lower + sem acento) e casa por substring. Se casar os DOIS lados (ambíguo) ou
    NENHUM, devolve None em vez de chutar — e loga o título pra ajudar o diagnóstico.
    """
    if not titulo:
        return None
    texto = _normalize(titulo)
    tem_aluguel = any(termo in texto for termo in _TERMOS_ALUGUEL)
    tem_compra = any(termo in texto for termo in _TERMOS_COMPRA)
    if tem_aluguel == tem_compra:  # ambos (ambíguo) ou nenhum → não resolvido
        logger.info("Navent: título não resolveu a operação (ambíguo/sem termo): %r", titulo)
        return None
    return "Aluguel" if tem_aluguel else "Compra"


def _base() -> str:
    return settings.navent_base_url.rstrip("/")


def _auth_headers() -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.navent_token}",
    }


def _get_json(url: str) -> dict | list | None:
    """GET que devolve o JSON decodificado, ou None em qualquer falha (best-effort)."""
    resp = requests.get(url, headers=_auth_headers(), params=None, timeout=_TIMEOUT)
    if not resp.ok:
        # A URL NÃO carrega o token (ele vai no header Authorization), então é seguro
        # logá-la. Sem este aviso, um token expirado / path mudado / contrato quebrado
        # faz TODO lead Wimóveis gravar transaction_type=NULL para sempre, sem sinal.
        logger.warning("Navent GET %s devolveu HTTP %s", url, resp.status_code)
        return None
    return resp.json()


def _itens(resumo: dict | list | None) -> list:
    """Extrai a lista de anúncios do resumo, tolerante ao envelope.

    O endpoint pode devolver a lista direta ou embrulhada num objeto
    (`content`/`data`/`items`); pegamos a 1ª lista que acharmos. Sempre devolve uma
    lista (vazia se não achar) — assim o chamador só precisa checar `if not itens`.
    """
    if isinstance(resumo, list):
        return resumo
    if isinstance(resumo, dict):
        for chave in ("content", "data", "items"):
            valor = resumo.get(chave)
            if isinstance(valor, list):
                return valor
        # Fallback: 1ª lista presente em qualquer valor do dict (envelope desconhecido).
        lista = next((v for v in resumo.values() if isinstance(v, list)), None)
        if lista is not None:
            return lista
    return []


def fetch_operation(idnavplat: int | None, codigo_imobiliaria: str | None) -> str | None:
    """Resolve "Compra" | "Aluguel" | None para um aviso do Wimóveis via Navent.

    Best-effort e à prova de falha: sem token/identificadores, ou em qualquer erro
    de rede/HTTP/formato, devolve None sem levantar.
    """
    # Guard: sem credencial ou sem os identificadores, nem toca a rede.
    if not settings.navent_token or not idnavplat or not codigo_imobiliaria:
        return None

    try:
        base = _base()
        # Escapa o código no path (quote com safe="") — um valor malformado não desvia
        # a chamada. idnavplat é int (interpolação segura), não precisa escapar.
        cod = quote(codigo_imobiliaria, safe="")
        # 1 GET só: resumo dos anúncios online → título do aviso pedido.
        resumo_url = (
            f"{base}/v1/imobiliarias/{cod}/anuncios/online/resumo"
            f"?idAvisoNavplat={idnavplat}"
        )
        itens = _itens(_get_json(resumo_url))
        if not itens:
            logger.info(
                "Navent: resumo vazio (idnavplat=%s, imobiliaria=%s) — tipo não resolvido",
                idnavplat, codigo_imobiliaria,
            )
            return None

        primeiro = itens[0]
        titulo = primeiro.get("titulo") if isinstance(primeiro, dict) else None
        # _operacao_do_titulo já loga o título quando não resolve (ambíguo/sem termo).
        return _operacao_do_titulo(titulo)
    except Exception as exc:  # noqa: BLE001 — enriquecimento opcional; NUNCA derruba a ingestão
        logger.warning(
            "fetch_operation falhou para idnavplat=%s: %s", idnavplat, exc, exc_info=True
        )
        return None
