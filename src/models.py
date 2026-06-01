"""Schemas Pydantic dos leads.

- `WimoveisLead`: payload cru do webhook do Wimóveis (campos oficiais do leadManager).
- `Lead`: lead canônico, normalizado entre portais — é o formato gravado no DuckDB.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WimoveisLead(BaseModel):
    """Payload cru do webhook do Wimóveis (Grupo OLX / leadManager).

    Os nomes oficiais vêm em PascalCase; mapeamos via alias para snake_case.
    `populate_by_name` permite construir tanto pelo alias quanto pelo nome.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str = Field(alias="LeadName")
    email: str | None = Field(default=None, alias="LeadEmail")
    phone: str | None = Field(default=None, alias="LeadTelephone")
    message: str | None = Field(default=None, alias="Message")
    external_id: str = Field(alias="ExternalId")
    business_type: str | None = Field(default=None, alias="BusinessType")
    broker_email: str | None = Field(default=None, alias="BrokerEmail")
    origin: str | None = Field(default=None, alias="LeadOrigin")


class Lead(BaseModel):
    """Lead canônico — uma linha da tabela `leads_raw`.

    `raw_payload` guarda o JSON original recebido (rastreabilidade/auditoria).
    Dedup é feita por (`source`, `external_id`).
    """

    external_id: str
    source: str  # 'wimoveis' | 'dfimoveis'
    name: str
    email: str | None = None
    phone: str | None = None
    message: str | None = None
    business_type: str | None = None
    broker_email: str | None = None
    origin: str | None = None
    raw_payload: str
    received_at: datetime
