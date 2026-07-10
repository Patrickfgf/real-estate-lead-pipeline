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
    # Etiquetas de temperatura do lead (Fase 3 — lead scoring). IDs gerados por
    # `python -m src.trello setup`.
    trello_label_quente: str = os.getenv("TRELLO_LABEL_QUENTE", "")
    trello_label_morno: str = os.getenv("TRELLO_LABEL_MORNO", "")
    trello_label_frio: str = os.getenv("TRELLO_LABEL_FRIO", "")
    # Nomes do quadro / área de trabalho do Trello (usados pelo `setup`). Genéricos por
    # padrão para o repositório público; em produção, defina os nomes reais no .env.
    trello_workspace_name: str = os.getenv("TRELLO_WORKSPACE_NAME", "Leads CRM")
    trello_board_name: str = os.getenv("TRELLO_BOARD_NAME", "Leads — Imobiliária")

    # ---------- Trello — roteamento por 2 quadros (Fase 2) ----------
    # Cada card vai para o quadro do seu `transaction_type`: Compra → quadro de Compra,
    # Aluguel → quadro de Locação. `transaction_type` None/desconhecido (ex.: Wimóveis
    # quando a Navent não resolveu) cai no quadro ÚNICO acima (`trello_list_id`).
    # ROLLOUT SEGURO: enquanto o list id do quadro estiver vazio (estado atual), o
    # roteamento fica INATIVO e tudo cai no fallback — nada quebra antes do `setup`.
    trello_board_name_compra: str = os.getenv("TRELLO_BOARD_NAME_COMPRA", "Leads — Compra")
    trello_board_name_aluguel: str = os.getenv("TRELLO_BOARD_NAME_ALUGUEL", "Leads — Locação")
    # Listas de entrada de cada quadro (o list id ativa/desativa o roteamento do tipo).
    trello_list_id_compra: str = os.getenv("TRELLO_LIST_ID_COMPRA", "")
    trello_list_id_aluguel: str = os.getenv("TRELLO_LIST_ID_ALUGUEL", "")
    # Etiquetas POR-QUADRO (origem + temperatura). Um id de etiqueta pertence a UM
    # quadro só — por isso o conjunto é duplicado (um para Compra, outro para Locação);
    # resolver a etiqueta junto com o quadro evita o add_label bater em id de outro quadro.
    trello_label_wimoveis_compra: str = os.getenv("TRELLO_LABEL_WIMOVEIS_COMPRA", "")
    trello_label_dfimoveis_compra: str = os.getenv("TRELLO_LABEL_DFIMOVEIS_COMPRA", "")
    trello_label_quente_compra: str = os.getenv("TRELLO_LABEL_QUENTE_COMPRA", "")
    trello_label_morno_compra: str = os.getenv("TRELLO_LABEL_MORNO_COMPRA", "")
    trello_label_frio_compra: str = os.getenv("TRELLO_LABEL_FRIO_COMPRA", "")
    trello_label_wimoveis_aluguel: str = os.getenv("TRELLO_LABEL_WIMOVEIS_ALUGUEL", "")
    trello_label_dfimoveis_aluguel: str = os.getenv("TRELLO_LABEL_DFIMOVEIS_ALUGUEL", "")
    trello_label_quente_aluguel: str = os.getenv("TRELLO_LABEL_QUENTE_ALUGUEL", "")
    trello_label_morno_aluguel: str = os.getenv("TRELLO_LABEL_MORNO_ALUGUEL", "")
    trello_label_frio_aluguel: str = os.getenv("TRELLO_LABEL_FRIO_ALUGUEL", "")


settings = Settings()
