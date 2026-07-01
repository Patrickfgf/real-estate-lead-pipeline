"""Cadastro do callback de leads na Navent Open API (Imovelweb/Wimóveis).

ATENÇÃO — dois "segredos" diferentes, NÃO confundir:
  • TOKEN da Navent (NAVENT_TOKEN): autentica NÓS chamando a API DELES. É gerado
    no playground (ver vídeo Loom) ou via login usuário/senha.
  • Segredo do webhook (WIMOVEIS_WEBHOOK_SECRET): é o que a Navent vai mandar de
    volta pra NÓS em cada callback, no header `x-webhook-token`, pra provarmos que
    o POST veio mesmo deles. Casa com `_check_secret` em src/ingest_wimoveis.py.

Fluxo: usa o TOKEN -> PUT /v1/configuracion/callbacks com a URL pública do nosso
webhook + o header de autorização que a Navent deve nos reenviar.

O `register` faz read-modify-write: lê a config atual (GET) e reescreve só os 3
campos que mudam, preservando o resto (subscriptions, lenguajeCallbackBody, ...).
Se não conseguir ler a config atual, ABORTA (use --force só no 1º cadastro).

CLI:
    python -m src.navent show               # GET: mostra a config de callback atual
    python -m src.navent register            # GET+PUT: reaponta preservando a config atual
    python -m src.navent register --dry-run  # lê a config (GET) e imprime o corpo do PUT (exige token)
    python -m src.navent register --force    # PUT sem LER a config (1º cadastro/404 — RISCO: pode apagar dados)
    python -m src.navent delete CONTACTO     # DELETE: desinscreve um evento
    python -m src.navent login               # (best-effort) login usuário/senha -> imprime token

Doc: https://open.navent.com/guias/callbacks/introduccion
"""
import json
import sys

import requests

from src.config import settings

_TIMEOUT = 20

# --- Caminhos da API (confirmados na doc, exceto onde marcado CONFIRMAR) ---
# ATENÇÃO grafia: o Swagger de produção do Brasil (api-br-open.navent.com, grupo
# realestate) documenta o recurso como "/v1/configuracao/callbacks" (PORTUGUÊS).
# O caminho espanhol abaixo é o que já vinha em uso; se `python -m src.navent show`
# devolver 404, troque para a grafia PT. O register é fail-safe: 404 aborta (não
# reescreve) a menos de --force, então uma grafia errada NÃO apaga a config.
_CALLBACKS_PATH = "/v1/configuracion/callbacks"   # doc PT: /v1/configuracao/callbacks
_WEBHOOK_ROUTE = "/webhook/wimoveis"              # a nossa rota que recebe o lead
_LOGIN_PATH = "/v1/login"                         # CONFIRMAR no playground/Loom (login best-effort)

# Campos do callback que NÓS montamos (o resto vem do GET e é preservado no PUT).
_OWNED_KEYS = ("url", "authorizationHeaderKey", "authorizationHeaderValue")
# Campos sensíveis a mascarar antes de imprimir qualquer corpo de resposta.
_SENSITIVE_KEYS = ("authorizationHeaderValue",)

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


def build_callback_config(
    public_url: str,
    secret: str,
    header_key: str = _CALLBACK_HEADER_KEY,
    existing: dict | None = None,
) -> dict:
    """Monta o corpo do PUT de cadastro do callback. Função pura (testável).

    `url`: endpoint público que recebe o POST (nossa URL + a rota do webhook).
    `authorizationHeaderKey`/`Value`: o header (nome + segredo) que a Navent deve
    reenviar pra nós em cada callback, pra autenticar a origem.

    `existing`: a config atual devolvida pela Navent (GET). O PUT é full-replace,
    então partimos dela e sobrescrevemos SÓ os 3 campos que mudam — assim campos
    que a Navent guarda e nós não montamos (ex.: `subscriptions`,
    `lenguajeCallbackBody`) sobrevivem ao re-cadastro em vez de serem apagados.
    """
    base = public_url.rstrip("/")
    config = dict(existing) if existing else {}
    config.update(
        {
            "url": f"{base}{_WEBHOOK_ROUTE}",
            "authorizationHeaderKey": header_key,
            "authorizationHeaderValue": secret,
        }
    )
    return config


def _require(condition: bool, msg: str) -> None:
    if not condition:
        print(f"ERRO: {msg}", file=sys.stderr)
        sys.exit(2)


def _redact_body(text: str) -> str:
    """Mascara segredos (ex.: authorizationHeaderValue) num corpo JSON antes de
    imprimir. Se não for JSON, devolve o texto como veio."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text

    def _walk(obj):
        if isinstance(obj, dict):
            return {
                k: (_mask(str(v)) if k in _SENSITIVE_KEYS and v else _walk(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_walk(x) for x in obj]
        return obj

    return json.dumps(_walk(data), ensure_ascii=False)


def _print_response(resp: requests.Response) -> None:
    print(f"  <- HTTP {resp.status_code}")
    body = resp.text.strip()
    if body:
        print(f"     {_redact_body(body)[:1000]}")


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


def _looks_like_callback(cfg: dict) -> bool:
    """O dict devolvido pelo GET parece uma config de callback (e não um envelope)?"""
    return "url" in cfg or "authorizationHeaderKey" in cfg


def _fetch_current_config() -> dict | None:
    """GET da config de callback atual, para o read-modify-write do register.

    Retorna o dict da config quando consegue LER POSITIVAMENTE uma config
    existente. Em qualquer outro caso — 404, erro de auth/servidor, falha de rede,
    corpo não-JSON, formato inesperado — retorna None. O chamador trata None como
    "não consegui ler" e ABORTA (a menos de --force): assim nunca mandamos um PUT
    "pelado" que apagaria subscriptions/lenguajeCallbackBody por engano (p.ex. um
    caminho errado devolvendo 404, ou um 500 transitório).
    """
    url = f"{_base()}{_CALLBACKS_PATH}"
    print(f"GET {url}  (lendo a config atual antes de reescrever)")
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        print(f"  falha de rede no GET: {exc}", file=sys.stderr)
        return None
    _print_response(resp)
    if not resp.ok:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        return data if _looks_like_callback(data) else None
    if isinstance(data, list):
        # Defensivo: o spec do Brasil devolve um objeto único, mas se vier lista,
        # pegamos o callback do NOSSO webhook; se não achar, None (fail-safe).
        for c in data:
            if isinstance(c, dict) and str(c.get("url", "")).endswith(_WEBHOOK_ROUTE):
                return c
    return None


def cmd_register(dry_run: bool = False, force: bool = False) -> None:
    """PUT que cadastra/atualiza o callback para o nosso webhook.

    Read-modify-write: lê a config atual (GET) e sobrescreve SÓ os 3 campos que
    mudam (`url` + os 2 de auth). Assim o PUT (full-replace) NÃO apaga o que a
    Navent já guarda e nós não montamos — p.ex. `subscriptions` e
    `lenguajeCallbackBody`.

    Se NÃO conseguir ler uma config existente (inclusive 404), ABORTA — a menos de
    --force. O --force manda um corpo MÍNIMO e é o que o 1º cadastro (404) exige;
    é também o único caminho capaz de apagar campos, então use com cuidado.
    """
    _require(
        bool(settings.webhook_public_url),
        "WEBHOOK_PUBLIC_URL não configurado no .env (a URL pública do nosso webhook)",
    )
    _require(
        bool(settings.wimoveis_webhook_secret),
        "WIMOVEIS_WEBHOOK_SECRET não configurado no .env",
    )
    _require(
        bool(settings.navent_token),
        "NAVENT_TOKEN não configurado no .env (preciso dele já pra ler a config atual)",
    )

    existing = _fetch_current_config()
    if existing is None:
        _require(
            force,
            "não LI nenhuma config de callback existente (veja o HTTP acima). Se for "
            "1º cadastro mesmo, rode com --force. Se a config já existe, um PUT agora "
            "apagaria subscriptions/lenguajeCallbackBody — cheque caminho/URL/token antes.",
        )
        print(
            "AVISO: --force sem config lida — enviando corpo MÍNIMO. Se já existia um "
            "callback, isto APAGA subscriptions/lenguajeCallbackBody."
        )

    body = build_callback_config(
        settings.webhook_public_url, settings.wimoveis_webhook_secret, existing=existing
    )
    url = f"{_base()}{_CALLBACKS_PATH}"
    preservados = {k: v for k, v in body.items() if k not in _OWNED_KEYS}

    print(f"PUT {url}")
    print(f"  callback url ......... {body['url']}")
    print(f"  authorizationHeaderKey {body['authorizationHeaderKey']}")
    print(f"  authorizationHeaderValue {_mask(body['authorizationHeaderValue'])}  (o segredo do webhook)")
    print(f"  preservados do GET ... {preservados if preservados else '(nenhum)'}")

    if dry_run:
        print("\n[dry-run] nada foi enviado. Confira a URL e o corpo acima.")
        return

    try:
        resp = requests.put(url, json=body, headers=_auth_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        _require(False, f"falha de rede no PUT: {exc}")
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
        extras = [a for a in argv[1:] if a not in ("--dry-run", "--force")]
        if extras:
            print(f"opção desconhecida em register: {extras[0]} (use --dry-run e/ou --force)", file=sys.stderr)
            sys.exit(1)
        cmd_register(dry_run="--dry-run" in argv[1:], force="--force" in argv[1:])
    elif cmd == "delete":
        cmd_delete(argv[1] if len(argv) > 1 else "")
    elif cmd == "login":
        cmd_login()
    else:
        print(
            "uso: python -m src.navent [show | register [--dry-run] [--force] | delete <EVENTO> | login]",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
