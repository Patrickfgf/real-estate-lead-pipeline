"""Cadastro do callback de leads na Navent Open API (Imovelweb/Wimóveis).

ATENÇÃO — dois "segredos" diferentes, NÃO confundir:
  • TOKEN da Navent (NAVENT_TOKEN): autentica NÓS chamando a API DELES. É gerado
    no playground (ver vídeo Loom) ou via login usuário/senha.
  • Segredo do webhook (WIMOVEIS_WEBHOOK_SECRET): é o que a Navent vai mandar de
    volta pra NÓS em cada callback, no header `x-webhook-token`, pra provarmos que
    o POST veio mesmo deles. Casa com `_check_secret` em src/ingest_wimoveis.py.

Fluxo: usa o TOKEN -> PUT /v1/configuracion/callbacks com a URL pública do nosso
webhook + o header de autorização que a Navent deve nos reenviar.

CLI:
    python -m src.navent show               # GET: mostra a config de callback atual
    python -m src.navent register            # PUT: cadastra/atualiza o callback
    python -m src.navent register --dry-run  # só imprime o que enviaria (não chama a API)
    python -m src.navent delete CONTACTO     # DELETE: desinscreve um evento
    python -m src.navent login               # (best-effort) login usuário/senha -> imprime token

Doc: https://open.navent.com/guias/callbacks/introduccion
"""
import sys

import requests

from src.config import settings

_TIMEOUT = 20

# --- Caminhos da API (confirmados na doc, exceto onde marcado CONFIRMAR) ---
_CALLBACKS_PATH = "/v1/configuracion/callbacks"   # doc: PUT cadastra, DELETE /{evento} desinscreve
_WEBHOOK_ROUTE = "/webhook/wimoveis"              # a nossa rota que recebe o lead
_LOGIN_PATH = "/v1/login"                         # CONFIRMAR no playground/Loom (login best-effort)

# Como a Navent deve nos autenticar no callback: cabeçalho que ela reenvia em
# cada POST. Mantemos "x-webhook-token" para casar com _check_secret sem mudar código.
_CALLBACK_HEADER_KEY = "x-webhook-token"

# Como NÓS autenticamos chamando a API da Navent.
# CONFIRMADO empiricamente (2026-06-01): "Authorization: Bearer <token>" passa pela
# camada de segurança (qualquer outro esquema dá 401 "Authentication object not found").
_TOKEN_SCHEME = "Bearer"


def _base() -> str:
    return settings.navent_base_url.rstrip("/")


def _mask(value: str) -> str:
    """Esconde o miolo de um segredo para impressão segura."""
    if not value:
        return "(vazio)"
    if len(value) <= 8:
        return value[0] + "***"
    return f"{value[:4]}…{value[-4:]}"


def _auth_headers() -> dict:
    """Header de autenticação NOSSA -> API da Navent (usa NAVENT_TOKEN)."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = settings.navent_token
    if token:
        headers["Authorization"] = f"{_TOKEN_SCHEME} {token}".strip()
    return headers


def build_callback_config(public_url: str, secret: str, header_key: str = _CALLBACK_HEADER_KEY) -> dict:
    """Monta o corpo do PUT de cadastro do callback. Função pura (testável).

    `url`: endpoint público que recebe o POST (nossa URL + a rota do webhook).
    `authorizationHeaderKey`/`Value`: o header (nome + segredo) que a Navent deve
    reenviar pra nós em cada callback, pra autenticar a origem.
    """
    base = public_url.rstrip("/")
    return {
        "url": f"{base}{_WEBHOOK_ROUTE}",
        "authorizationHeaderKey": header_key,
        "authorizationHeaderValue": secret,
    }


def _require(condition: bool, msg: str) -> None:
    if not condition:
        print(f"ERRO: {msg}", file=sys.stderr)
        sys.exit(2)


def _print_response(resp: requests.Response) -> None:
    print(f"  <- HTTP {resp.status_code}")
    body = resp.text.strip()
    if body:
        print(f"     {body[:1000]}")


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
def cmd_show() -> None:
    """GET da configuração de callbacks atual."""
    _require(bool(settings.navent_token), "NAVENT_TOKEN não configurado no .env")
    url = f"{_base()}{_CALLBACKS_PATH}"
    print(f"GET {url}  (Authorization: {_TOKEN_SCHEME} {_mask(settings.navent_token)})")
    resp = requests.get(url, headers=_auth_headers(), timeout=_TIMEOUT)
    _print_response(resp)


def cmd_register(dry_run: bool = False) -> None:
    """PUT que cadastra/atualiza o callback para o nosso webhook."""
    _require(bool(settings.webhook_public_url), "WEBHOOK_PUBLIC_URL não configurado no .env (a URL do túnel/VPS)")
    _require(bool(settings.wimoveis_webhook_secret), "WIMOVEIS_WEBHOOK_SECRET não configurado no .env")

    body = build_callback_config(settings.webhook_public_url, settings.wimoveis_webhook_secret)
    url = f"{_base()}{_CALLBACKS_PATH}"

    print(f"PUT {url}")
    print(f"  callback url ......... {body['url']}")
    print(f"  authorizationHeaderKey {body['authorizationHeaderKey']}")
    print(f"  authorizationHeaderValue {_mask(body['authorizationHeaderValue'])}  (o segredo do webhook)")

    if dry_run:
        print("\n[dry-run] nada foi enviado. Confira a URL e o corpo acima.")
        return

    _require(bool(settings.navent_token), "NAVENT_TOKEN não configurado no .env (gere no playground/Loom)")
    resp = requests.put(url, json=body, headers=_auth_headers(), timeout=_TIMEOUT)
    _print_response(resp)
    if resp.ok:
        print("\nOK — callback cadastrado. Deixe o webhook no ar e dispare um lead de teste no sandbox.")


def cmd_delete(evento: str) -> None:
    """DELETE que desinscreve um evento (ex.: CONTACTO)."""
    _require(bool(evento), "informe o evento: python -m src.navent delete CONTACTO")
    _require(bool(settings.navent_token), "NAVENT_TOKEN não configurado no .env")
    url = f"{_base()}{_CALLBACKS_PATH}/{evento}"
    print(f"DELETE {url}")
    resp = requests.delete(url, headers=_auth_headers(), timeout=_TIMEOUT)
    _print_response(resp)


def cmd_login() -> None:
    """Best-effort: tenta login usuário/senha e imprime o token.

    O endpoint exato (_LOGIN_PATH) precisa ser confirmado no playground/Loom. Se
    falhar, gere o token manualmente no playground e cole em NAVENT_TOKEN no .env.
    """
    _require(bool(settings.navent_user and settings.navent_password),
             "NAVENT_USER/NAVENT_PASSWORD não configurados no .env")
    url = f"{_base()}{_LOGIN_PATH}"
    print(f"POST {url}  (login best-effort — confirmar endpoint no playground)")
    try:
        resp = requests.post(
            url,
            json={"usuario": settings.navent_user, "password": settings.navent_password},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"  falha de conexão: {exc}", file=sys.stderr)
        sys.exit(1)
    _print_response(resp)
    if resp.ok:
        print("\nSe veio um token acima, copie-o para NAVENT_TOKEN no .env.")


# ---------------------------------------------------------------------------
# Entrada CLI
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> None:
    for _stream in (sys.stdout, sys.stderr):  # console Windows (cp1252) tolerante a emoji/acentos
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    cmd = argv[0] if argv else ""
    if cmd == "show":
        cmd_show()
    elif cmd == "register":
        cmd_register(dry_run="--dry-run" in argv[1:])
    elif cmd == "delete":
        cmd_delete(argv[1] if len(argv) > 1 else "")
    elif cmd == "login":
        cmd_login()
    else:
        print(
            "uso: python -m src.navent [show | register [--dry-run] | delete <EVENTO> | login]",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
