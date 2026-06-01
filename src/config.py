"""Configuração central: carrega o .env e expõe as variáveis já tipadas.

Importar sempre o singleton `settings` (não ler os.getenv espalhado pelo código).
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Carrega o .env da raiz do projeto (se existir). Em produção as variáveis
# podem vir do ambiente direto — load_dotenv não sobrescreve o que já existe.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    # App / FastAPI
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    tz: str = os.getenv("TZ", "America/Sao_Paulo")

    # Banco (DuckDB)
    duckdb_path: str = os.getenv("DUCKDB_PATH", "data/jare.duckdb")

    # Wimóveis (webhook oficial)
    wimoveis_webhook_secret: str = os.getenv("WIMOVEIS_WEBHOOK_SECRET", "")

    # Navent Open API (login + cadastro do callback que entrega os leads)
    navent_base_url: str = os.getenv("NAVENT_BASE_URL", "https://api-br-sandbox-open.navent.com")
    navent_token: str = os.getenv("NAVENT_TOKEN", "")  # gerado no playground (vídeo Loom) ou via login
    navent_user: str = os.getenv("NAVENT_USER", "")
    navent_password: str = os.getenv("NAVENT_PASSWORD", "")
    # URL pública do NOSSO webhook (túnel cloudflared ou domínio do VPS), SEM barra final.
    webhook_public_url: str = os.getenv("WEBHOOK_PUBLIC_URL", "")

    # DFImóveis (webhook oficial — padrão VrSync, recebe POST)
    dfimoveis_webhook_secret: str = os.getenv("DFIMOVEIS_WEBHOOK_SECRET", "")

    # Trello
    trello_api_key: str = os.getenv("TRELLO_API_KEY", "")
    trello_api_token: str = os.getenv("TRELLO_API_TOKEN", "")
    trello_list_id: str = os.getenv("TRELLO_LIST_ID", "")
    trello_label_wimoveis: str = os.getenv("TRELLO_LABEL_WIMOVEIS", "")
    trello_label_dfimoveis: str = os.getenv("TRELLO_LABEL_DFIMOVEIS", "")


settings = Settings()
