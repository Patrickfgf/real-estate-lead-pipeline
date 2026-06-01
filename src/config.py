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

    # DFImóveis (API oficial — polling)
    dfimoveis_api_url: str = os.getenv("DFIMOVEIS_API_URL", "")
    dfimoveis_api_token: str = os.getenv("DFIMOVEIS_API_TOKEN", "")
    dfimoveis_poll_interval: int = int(os.getenv("DFIMOVEIS_POLL_INTERVAL", "300"))

    # Trello
    trello_api_key: str = os.getenv("TRELLO_API_KEY", "")
    trello_api_token: str = os.getenv("TRELLO_API_TOKEN", "")
    trello_list_id: str = os.getenv("TRELLO_LIST_ID", "")


settings = Settings()
