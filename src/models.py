"""Schemas Pydantic dos leads.

- `WimoveisContato`: payload cru do evento CONTACTO entregue pelo callback da
  Navent (plataforma do Imovelweb/Wimóveis). Campos oficiais em português.
- `Lead`: lead canônico, normalizado entre portais — formato gravado no DuckDB.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WimoveisContato(BaseModel):
    """Evento CONTACTO (lead) do callback da Navent — Imovelweb/Wimóveis.

    Os nomes camelCase compostos vêm via alias; os de palavra única
    (nome, email, telefone, mensagem, referencia, cpf) batem direto.
    `extra="allow"` preserva campos novos que a Navent venha a adicionar.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id_evento: str = Field(alias="idEvento")
    tipo_evento: str | None = Field(default=None, alias="tipoEvento")
    id_tipo_contato: int | None = Field(default=None, alias="idTipoContacto")
    nome: str
    email: str | None = None
    telefone: str | None = None
    mensagem: str | None = None
    referencia: str | None = None
    data_registro: str | None = Field(default=None, alias="dataRegistro")
    id_contato: int | None = Field(default=None, alias="idContato")
    codigo_imobiliaria: str | None = Field(default=None, alias="codigoImobiliaria")
    codigo_anunciante: str | None = Field(default=None, alias="codigoDoAnunciante")
    cpf: str | None = None


class Lead(BaseModel):
    """Lead canônico — uma linha da tabela `leads_raw`.

    `raw_payload` guarda o JSON original recebido (rastreabilidade/auditoria).
    Dedup é feita por (`source`, `external_id`).
    """

    external_id: str  # idEvento, no caso do Wimóveis
    source: str  # 'wimoveis' | 'dfimoveis'
    name: str
    email: str | None = None
    phone: str | None = None
    message: str | None = None
    listing_ref: str | None = None  # referencia (código do anúncio)
    advertiser_code: str | None = None  # codigoDoAnunciante (corretor/anunciante)
    agency_code: str | None = None  # codigoImobiliaria
    cpf: str | None = None
    lead_date: datetime | None = None  # dataRegistro (quando o lead ocorreu na origem)
    raw_payload: str
    received_at: datetime  # quando nós recebemos/ingerimos
