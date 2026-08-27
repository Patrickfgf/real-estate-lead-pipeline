"""Schemas Pydantic dos leads.

- `WimoveisContato`: payload cru do evento CONTACTO entregue pelo callback da
  Navent (plataforma do Imovelweb/Wimóveis). Campos oficiais em português.
- `Lead`: lead canônico, normalizado entre portais — formato gravado no DuckDB.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    id_navplat: int | None = Field(default=None, alias="idnavplat")  # ID do aviso na Navent (o imóvel)
    codigo_imobiliaria: str | None = Field(default=None, alias="codigoImobiliaria")
    codigo_anunciante: str | None = Field(default=None, alias="codigoDoAnunciante")
    cpf: str | None = None


class VrSyncLead(BaseModel):
    """Lead do webhook VrSync (GrupoZAP/OLX) — padrão aceito pela DFImóveis.

    A DFImóveis faz POST deste payload na nossa URL. Só `originLeadId` e `name`
    são obrigatórios; o resto é opcional. `extra="allow"` preserva campos novos.
    Doc: https://developers.grupozap.com/webhooks/integration_leads.html
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    origin_lead_id: str = Field(alias="originLeadId")
    lead_origin: str | None = Field(default=None, alias="leadOrigin")
    timestamp: str | None = None
    origin_listing_id: str | None = Field(default=None, alias="originListingId")
    client_listing_id: str | None = Field(default=None, alias="clientListingId")
    name: str
    email: str | None = None
    ddd: str | None = None
    phone: str | None = None
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    message: str | None = None
    temperature: str | None = None  # Baixa/Média/Alta — útil na Fase 3 (lead scoring)
    transaction_type: str | None = Field(default=None, alias="transactionType")
    # URL pública do anúncio. Campo NÃO-canônico do VrSync: a DFImóveis o adicionou
    # em 2026-08 a nosso pedido (a doc do GrupoZAP não o lista). Por isso é opcional
    # e passa por validação de domínio antes de virar link no card.
    listing_url: str | None = Field(default=None, alias="listingUrl")

    @field_validator("listing_url", mode="before")
    @classmethod
    def _tolera_listing_url_nao_string(cls, v: object) -> str | None:
        """Um `listingUrl` de tipo inesperado vira None em vez de invalidar o lead.

        Sem isso, um `listingUrl: 247550` (o id numérico em vez da URL) levantaria
        ValidationError e mandaria o lead INTEIRO — nome, telefone, e-mail — para a
        caixa de revisão, com 422 para o portal. Perder a URL é aceitável; perder o
        lead não. É justamente o campo mais novo e o único não-canônico do VrSync,
        logo o mais provável de vir com formato inconsistente. O valor cru continua
        no `raw_payload`.
        """
        return v if isinstance(v, str) else None

    @property
    def telefone_completo(self) -> str | None:
        """phoneNumber se houver; senão junta ddd + phone."""
        if self.phone_number:
            return self.phone_number
        if self.ddd and self.phone:
            return f"{self.ddd}{self.phone}"
        return self.phone or None


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
    transaction_type: str | None = None  # "Compra" | "Aluguel" | None — resolvido na ingestão
    listing_url: str | None = None  # URL pública do anúncio, quando o portal a envia
