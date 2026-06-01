# Projeto Jaré — Pipeline de Ingestão de Leads Imobiliários

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?logo=pydantic&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?logo=duckdb&logoColor=black)
![Trello API](https://img.shields.io/badge/Trello%20API-0052CC?logo=trello&logoColor=white)
![pytest](https://img.shields.io/badge/tested%20with-pytest-0A9EDC?logo=pytest&logoColor=white)

Pipeline de dados que **ingere leads de portais imobiliários** (**Wimóveis** via
webhook oficial e **DFImóveis** via API oficial), valida, deduplica e persiste em
**DuckDB**, pontua a qualidade do lead e os carrega no **Trello** (funil de vendas)
e num **dashboard** — para que o time comercial atue rápido sem perder lead.

> **Contexto de negócio.** Uma corretora em Brasília recebe leads pulverizados em
> vários portais. Sem um ponto único, leads esfriam ou se perdem. O Jaré
> centraliza a entrada, evita duplicatas e entrega cada lead já como um card
> acionável no funil do Trello.

---

## Arquitetura

```
  Wimóveis (webhook / push) ──┐
                              │
  DFImóveis (API / polling) ──┤   [planejado]
                              ▼
                     ┌─────────────────┐
                     │  FastAPI         │  ingestão HTTP
                     │  + validação     │  (Pydantic: contrato do payload)
                     └────────┬─────────┘
                              ▼
                     ┌─────────────────┐
                     │  DuckDB          │  persistência + dedup idempotente
                     │  (leads_raw)     │  PK (source, external_id)
                     └────────┬─────────┘
                              ▼
              limpeza / dedup → lead scoring   [planejado]
                              ▼
                ┌──────────────┴──────────────┐
                ▼                              ▼
        ┌───────────────┐            ┌──────────────────┐
        │  Trello        │            │  Dashboard        │
        │  (1 card/lead) │            │  (Streamlit)      │  [planejado]
        └───────────────┘            └──────────────────┘
```

A carga no Trello é **idempotente** e roda automaticamente quando um lead novo é
ingerido (best-effort: falha na carga não derruba a ingestão).

---

## Stack técnica

| Camada | Tecnologia | Papel no projeto |
|---|---|---|
| Linguagem | **Python 3.11+** | Base de todo o pipeline |
| API / Ingestão | **FastAPI** + **Uvicorn** | Recebe o webhook do portal (event-driven) |
| Validação | **Pydantic** | Contrato do payload; normaliza para o lead canônico |
| Persistência | **DuckDB** | Banco analítico embarcado; dedup via `PRIMARY KEY` |
| Integração | **Trello REST API** (via `requests`) | Cria os cards no funil + "infra como código" do quadro |
| Config | **python-dotenv** | Variáveis de ambiente / segredos fora do código |
| Testes | **pytest** + **httpx** (`TestClient`) | Testes de ingestão e de carga, isolados de serviços externos |
| _Planejado (Fases 2–5)_ | **pandas**, **Streamlit**, **Plotly** | Limpeza/enriquecimento, dashboard e visualização |

---

## Status & roadmap

| Fase | Entrega | Status |
|---|---|:---:|
| 0 | Esqueleto do projeto | ✅ |
| 1 | Ingestão Wimóveis (callback `CONTACTO` da Navent → DuckDB com dedup) + testes | ✅ |
| 4 | Carga no Trello (1 card por lead, idempotente) + setup do quadro como código | ✅ |
| 1b | Ingestão DFImóveis (polling com token) | ⬜ |
| 2 | Limpeza / dedup / enriquecimento | ⬜ |
| 3 | Lead scoring (qualidade do lead) | ⬜ |
| 5 | Dashboard Streamlit | ⬜ |
| 6 | Orquestração / deploy | ⬜ |

> A **Fase 4 foi antecipada** para fechar uma fatia vertical — *lead entra → card
> sai no Trello* — e entregar valor cedo. As Fases 2–3 (limpeza/scoring) entram
> depois, antes da criação do card.

---

## Estrutura do projeto

```
.
├── src/
│   ├── config.py            # Settings central (carrega o .env, tipado)
│   ├── models.py            # Schemas Pydantic: WimoveisContato (cru) e Lead (canônico)
│   ├── db.py                # DuckDB: conexão singleton, schema, insert com dedup
│   ├── ingest_wimoveis.py   # Rota FastAPI do webhook Wimóveis
│   ├── trello.py            # Carga idempotente + setup do quadro (também é CLI)
│   └── main.py              # App FastAPI (monta rotas + /health)
├── tests/
│   ├── conftest.py          # Isola os testes: DuckDB temporário, sem credenciais reais
│   ├── test_ingest_wimoveis.py
│   └── test_trello.py
├── samples/
│   └── wimoveis_lead.json   # Payload de exemplo para teste manual
├── start.py                 # Sobe o servidor (uvicorn, com reload)
├── testar_local.ps1         # Teste manual ponta a ponta (Windows / PowerShell)
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

---

## Como rodar (local)

```powershell
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt

copy .env.example .env          # Windows  (Linux/Mac: cp) e preencher os valores
python start.py
```

- **Swagger (docs interativas):** http://localhost:8000/docs
- **Health check:** http://localhost:8000/health

---

## Teste manual ponta a ponta

### Opção A — script automatizado (recomendado, Windows)

```powershell
.\testar_local.ps1
```

O script sobe o servidor, dispara o lead de exemplo no webhook, reenvia para
checar a deduplicação, derruba o servidor e consulta o DuckDB para confirmar a
gravação e o vínculo com o card. Imprime `[OK]`/`[XX]` por verificação e sai com
código `0` (tudo passou) ou `1` (alguma falha). Aceita `-Port` e `-TimeoutSec`.

> ⚠️ Com as credenciais do Trello preenchidas no `.env`, o lead de teste **cria um
> card real** no quadro (idempotente — reenviar não duplica). Para testar sem
> tocar no Trello, esvazie temporariamente as variáveis `TRELLO_*` no `.env`.

### Opção B — manual, passo a passo

Com o servidor no ar, simule a chegada de um lead (PowerShell):

```powershell
$secret = "<WIMOVEIS_WEBHOOK_SECRET>"   # opcional: se vazio no .env, roda em modo dev
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/webhook/wimoveis?token=$secret" `
  -ContentType "application/json" -InFile "samples\wimoveis_lead.json"
```

Resposta esperada: `{ "status": "received", "external_id": "...", "duplicate": false }`.
Reenviar o mesmo `idEvento` devolve `"duplicate": true` (dedup funcionando).

---

## Configurar e testar o Trello

1. Pegue sua **key** e **token** em https://trello.com/app-key e preencha
   `TRELLO_API_KEY` / `TRELLO_API_TOKEN` no `.env`.
2. Crie/garanta o quadro, as listas do funil e as etiquetas de origem (idempotente):

   ```powershell
   python -m src.trello setup    # cria quadro + funil + etiquetas; imprime os IDs
   ```

   Copie os IDs sugeridos (`TRELLO_LIST_ID`, `TRELLO_LABEL_WIMOVEIS`) para o `.env`.
   Comandos auxiliares:

   ```powershell
   python -m src.trello check    # valida key/token (mostra o usuário autenticado)
   python -m src.trello lists    # lista todos os boards e listas com seus IDs
   python -m src.trello push     # envia ao Trello os leads ainda sem card
   ```

3. Com o servidor recebendo leads, a carga é automática.

A carga é **idempotente**: cada lead vira no máximo um card (vínculo em
`leads_raw.trello_card_id` + marcador `jare-ext:<source>:<external_id>` na
descrição do card).

---

## Modelo de dados

Tabela única `leads_raw` no DuckDB (uma linha por lead recebido):

| Coluna | Descrição |
|---|---|
| `source`, `external_id` | **Chave primária composta** — identidade do lead na origem (`idEvento`) |
| `name`, `email`, `phone`, `message` | Dados de contato do interessado |
| `listing_ref`, `advertiser_code`, `agency_code` | Anúncio, anunciante e imobiliária |
| `cpf` | Documento, quando informado |
| `lead_date` | Quando o lead ocorreu na origem (`dataRegistro`) |
| `received_at` | Quando nós ingerimos |
| `raw_payload` | JSON original recebido (rastreabilidade / auditoria) |
| `trello_card_id` | Vínculo com o card criado (`NULL` = pendente de carga) |

---

## Decisões de engenharia

- **Ingestão event-driven** via webhook (push), com validação de segredo
  compartilhado (header ou query) — sem segredo configurado, cai em modo dev.
- **Contrato validado com Pydantic**: aliases mapeiam o `camelCase` da Navent para
  `snake_case`; `extra="allow"` mantém compatibilidade com campos futuros.
- **Dedup idempotente no banco**: `PRIMARY KEY (source, external_id)` +
  `ON CONFLICT DO NOTHING ... RETURNING` — o `INSERT` informa se de fato gravou,
  sem leitura prévia (sem condição de corrida).
- **DuckDB single-writer**: conexão única serializada por `threading.Lock`,
  adequada ao volume (alguns leads/dia) e simples de operar.
- **Carga no Trello idempotente e best-effort**: o vínculo fica no banco; falha
  ao criar um card não derruba o webhook nem os demais leads — a próxima carga
  reenvia os pendentes.
- **"Infra como código" do quadro Trello**: `setup_board()` garante quadro,
  listas do funil e etiquetas de forma idempotente — reprodutível, sem cliques.
- **Testes isolados de serviços externos**: `conftest.py` usa um DuckDB temporário
  e zera as credenciais do Trello, garantindo que a suíte nunca crie cards reais.

---

## Testes

```powershell
pip install -r requirements-dev.txt
pytest
```

Cobre a ingestão (health, validação de segredo, mapeamento de campos, dedup,
payload inválido → 422) e a carga no Trello (montagem do card, marcador de
rastreio, idempotência).
