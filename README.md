# Projeto Jaré — Pipeline de Ingestão de Leads Imobiliários

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?logo=pydantic&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?logo=duckdb&logoColor=black)
![Trello API](https://img.shields.io/badge/Trello%20API-0052CC?logo=trello&logoColor=white)
![pytest](https://img.shields.io/badge/tested%20with-pytest-0A9EDC?logo=pytest&logoColor=white)

Pipeline de dados que **ingere leads de portais imobiliários** (**Wimóveis** via
callback oficial da Navent e **DFImóveis** via webhook oficial padrão VrSync),
valida, deduplica e persiste em **DuckDB**, pontua a qualidade do lead e os carrega
no **Trello** (funil de vendas) e num **dashboard** — para que o time comercial
atue rápido sem perder lead. Payloads que não validam não se perdem: vão para uma
**caixa de revisão** (dead-letter) para inspeção e reprocessamento.

> **Contexto de negócio.** Uma corretora em Brasília recebe leads pulverizados em
> vários portais. Sem um ponto único, leads esfriam ou se perdem. O Jaré
> centraliza a entrada, evita duplicatas e entrega cada lead já como um card
> acionável no funil do Trello.

---

## Arquitetura

```
  Wimóveis  ─ callback CONTACTO (Navent Open API) ─┐
                                                   │  webhook HTTP (push)
  DFImóveis ─ webhook VrSync (GrupoZAP/OLX) ───────┤
                                                   ▼
                                          ┌─────────────────┐
                                          │  FastAPI         │  ingestão HTTP
                                          │  + validação     │  (Pydantic: contrato do payload)
                                          └────────┬─────────┘
                                  válido  │        │  inválido
                                          ▼        ▼
                            ┌─────────────────┐  ┌────────────────────────┐
                            │  DuckDB          │  │  caixa de revisão       │
                            │  (leads_raw)     │  │  (leads_dead_letter)    │
                            │  dedup idempot.  │  │  nada se perde          │
                            └────────┬─────────┘  └────────────────────────┘
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

Os dois portais entregam o lead por **push** (POST HTTP) na nossa rota — não há
polling. A carga no Trello é **idempotente** e roda automaticamente quando um lead
novo é ingerido (best-effort: falha na carga não derruba a ingestão). Todo POST que
não passa na validação é guardado na **caixa de revisão** em vez de ser descartado.

---

## Stack técnica

| Camada | Tecnologia | Papel no projeto |
|---|---|---|
| Linguagem | **Python 3.11+** | Base de todo o pipeline |
| API / Ingestão | **FastAPI** + **Uvicorn** | Recebe os webhooks dos portais (event-driven, push) |
| Validação | **Pydantic** | Contrato dos payloads (Wimóveis e VrSync); normaliza para o lead canônico |
| Persistência | **DuckDB** | Banco analítico embarcado; dedup via `PRIMARY KEY` + caixa de revisão |
| Integração (saída) | **Trello REST API** (via `requests`) | Cria os cards no funil + "infra como código" do quadro |
| Integração (entrada) | **Navent Open API** (via `requests`) | Cadastra/gerencia o callback de leads do Wimóveis |
| Config | **python-dotenv** | Variáveis de ambiente / segredos fora do código |
| Testes | **pytest** + **httpx** (`TestClient`) | Testes de ingestão e de carga, isolados de serviços externos |
| _Planejado (Fases 2–5)_ | **pandas**, **Streamlit**, **Plotly** | Limpeza/enriquecimento, dashboard e visualização |

---

## Status & roadmap

| Fase | Entrega | Status |
|---|---|:---:|
| 0 | Esqueleto do projeto | ✅ |
| 1 | Ingestão Wimóveis (callback `CONTACTO` da Navent → DuckDB com dedup) + testes | ✅ |
| 1b | Ingestão DFImóveis (webhook VrSync → DuckDB com dedup) + testes | ✅ |
| — | Caixa de revisão (dead-letter) p/ payloads inválidos + logging de produção | ✅ |
| — | Cliente da Navent Open API (cadastro do callback de leads) | ✅ |
| 4 | Carga no Trello (1 card por lead, idempotente) + setup do quadro como código | ✅ |
| 2 | Limpeza / dedup / enriquecimento | ⬜ |
| 3 | Lead scoring (qualidade do lead) | ⬜ |
| 5 | Dashboard Streamlit | ⬜ |
| 6 | Orquestração / deploy | ⬜ |

> A **Fase 4 foi antecipada** para fechar uma fatia vertical — *lead entra → card
> sai no Trello* — e entregar valor cedo. As Fases 2–3 (limpeza/scoring) entram
> depois, antes da criação do card. Com os **dois portais já ingerindo**, o próximo
> salto de valor é analítico: scoring (Fase 3) e dashboard (Fase 5).

---

## Estrutura do projeto

```
.
├── src/
│   ├── config.py            # Settings central (carrega o .env, tipado)
│   ├── models.py            # Schemas Pydantic: WimoveisContato + VrSyncLead (crus) e Lead (canônico)
│   ├── db.py                # DuckDB: conexão singleton, schema, insert com dedup + caixa de revisão (CLI)
│   ├── ingest_wimoveis.py   # Rota FastAPI do webhook Wimóveis (callback CONTACTO da Navent)
│   ├── ingest_dfimoveis.py  # Rota FastAPI do webhook DFImóveis (padrão VrSync / GrupoZAP)
│   ├── navent.py            # Cliente da Navent Open API: cadastra o callback de leads (CLI)
│   ├── trello.py            # Carga idempotente + setup do quadro (também é CLI)
│   └── main.py              # App FastAPI (monta as rotas + /health + logging)
├── tests/
│   ├── conftest.py          # Isola os testes: DuckDB temporário, sem credenciais reais
│   ├── test_ingest_wimoveis.py
│   ├── test_ingest_dfimoveis.py
│   ├── test_navent.py
│   └── test_trello.py
├── samples/
│   ├── wimoveis_lead.json   # Payload de exemplo (Wimóveis) para teste manual
│   └── dfimoveis_lead.json  # Payload de exemplo (DFImóveis / VrSync) para teste manual
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
- **Webhook Wimóveis:** `POST /webhook/wimoveis` (callback `CONTACTO` da Navent)
- **Webhook DFImóveis:** `POST /webhook/dfimoveis` (padrão VrSync do GrupoZAP/OLX)

Ambas as rotas aceitam o segredo via header (`x-webhook-token`) ou query (`?token=`);
sem segredo configurado no `.env`, rodam em **modo dev** (validação pulada).

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
# Wimóveis (callback CONTACTO da Navent)
$secret = "<WIMOVEIS_WEBHOOK_SECRET>"   # opcional: se vazio no .env, roda em modo dev
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/webhook/wimoveis?token=$secret" `
  -ContentType "application/json" -InFile "samples\wimoveis_lead.json"

# DFImóveis (padrão VrSync)
$secret = "<DFIMOVEIS_WEBHOOK_SECRET>"  # opcional: se vazio no .env, roda em modo dev
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/webhook/dfimoveis?token=$secret" `
  -ContentType "application/json" -InFile "samples\dfimoveis_lead.json"
```

Resposta esperada: `{ "status": "received", "external_id": "...", "duplicate": false }`.
Reenviar o mesmo lead (`idEvento` no Wimóveis / `originLeadId` no DFImóveis) devolve
`"duplicate": true` (dedup funcionando). Um payload inválido devolve `422` e é
guardado na caixa de revisão (veja abaixo).

### Inspecionar a caixa de revisão (dead-letter)

Todo POST que não parseia/valida é preservado para conserto — nada se perde:

```powershell
python -m src.db dead-letter   # lista as últimas entradas guardadas (origem, erro, payload)
```

---

## Configurar e testar o Trello

1. Pegue sua **key** e **token** em https://trello.com/app-key e preencha
   `TRELLO_API_KEY` / `TRELLO_API_TOKEN` no `.env`.
2. Crie/garanta o quadro, as listas do funil e as etiquetas de origem (idempotente):

   ```powershell
   python -m src.trello setup    # cria quadro + funil + etiquetas; imprime os IDs
   ```

   Copie os IDs sugeridos (`TRELLO_LIST_ID`, `TRELLO_LABEL_WIMOVEIS`,
   `TRELLO_LABEL_DFIMOVEIS`) para o `.env` — cada origem entra no card com a sua
   própria etiqueta. Comandos auxiliares:

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

## Cadastrar o callback de leads na Navent (Wimóveis)

Para o Wimóveis começar a entregar leads, é preciso dizer à **Navent Open API**
para onde fazer o POST. O `src/navent.py` faz isso por código (CLI).

> ⚠️ **Dois segredos diferentes, não confundir:** `NAVENT_TOKEN` autentica *nós*
> chamando a API *deles* (gerado no playground / login). `WIMOVEIS_WEBHOOK_SECRET`
> é o que a Navent reenvia *para nós* em cada callback (header `x-webhook-token`),
> para provarmos que o POST veio mesmo deles.

```powershell
# 1. Exponha o webhook local com uma URL pública (túnel cloudflared ou domínio do VPS)
#    e preencha WEBHOOK_PUBLIC_URL no .env (sem barra final).
python -m src.navent show               # GET: mostra a config de callback atual
python -m src.navent register --dry-run # imprime o que seria enviado (não chama a API)
python -m src.navent register           # PUT: cadastra/atualiza o callback
python -m src.navent delete CONTACTO    # DELETE: desinscreve um evento
```

A DFImóveis não tem cadastro por API: a URL `POST /webhook/dfimoveis?token=...` é
informada no painel deles (padrão VrSync).

---

## Modelo de dados

Duas tabelas no DuckDB.

**`leads_raw`** — uma linha por lead recebido (o lead canônico, normalizado entre portais):

| Coluna | Descrição |
|---|---|
| `source`, `external_id` | **Chave primária composta** — origem (`wimoveis`/`dfimoveis`) + ID na origem (`idEvento` no Wimóveis, `originLeadId` no DFImóveis) |
| `name`, `email`, `phone`, `message` | Dados de contato do interessado |
| `listing_ref`, `advertiser_code`, `agency_code` | Anúncio, anunciante e imobiliária |
| `cpf` | Documento, quando informado |
| `lead_date` | Quando o lead ocorreu na origem (`dataRegistro` / `timestamp`) |
| `received_at` | Quando nós ingerimos |
| `raw_payload` | JSON original recebido (rastreabilidade / auditoria) |
| `trello_card_id` | Vínculo com o card criado (`NULL` = pendente de carga) |

**`leads_dead_letter`** — caixa de revisão: todo POST que não parseia/valida cai aqui:

| Coluna | Descrição |
|---|---|
| `received_at` | Quando o POST chegou |
| `source` | Origem que tentou entregar (`wimoveis`/`dfimoveis`) |
| `error` | Mensagem do erro de parsing/validação |
| `raw_payload` | Texto cru recebido (para inspeção e reprocessamento) |

---

## Decisões de engenharia

- **Ingestão event-driven** via webhook (push), com validação de segredo
  compartilhado (header ou query) — sem segredo configurado, cai em modo dev.
- **Contrato validado com Pydantic**: dois schemas crus (`WimoveisContato` para o
  callback CONTACTO da Navent, `VrSyncLead` para o padrão VrSync da DFImóveis)
  normalizados para um **lead canônico** (`Lead`) único; aliases mapeiam o
  `camelCase` de cada portal para `snake_case` e `extra="allow"` mantém
  compatibilidade com campos futuros.
- **Caixa de revisão (dead-letter)**: o corpo cru é lido *antes* do parsing; se o
  JSON não parsear ou não validar, vai para `leads_dead_letter` e a rota devolve
  `422` — nenhum lead some por vir num formato inesperado.
- **Dedup idempotente no banco**: `PRIMARY KEY (source, external_id)` +
  `ON CONFLICT DO NOTHING ... RETURNING` — o `INSERT` informa se de fato gravou,
  sem leitura prévia (sem condição de corrida).
- **DuckDB single-writer**: conexão única serializada por `threading.Lock`,
  adequada ao volume (alguns leads/dia) e simples de operar.
- **Carga no Trello idempotente e best-effort**: o vínculo fica no banco; falha
  ao criar um card não derruba o webhook nem os demais leads — a próxima carga
  reenvia os pendentes.
- **"Infra como código" do quadro Trello**: `setup_board()` garante quadro,
  listas do funil e etiquetas de origem (por portal) de forma idempotente —
  reprodutível, sem cliques.
- **Callback da Navent por código**: `src/navent.py` cadastra/gerencia o callback
  via API, separando claramente o token de *saída* (chamar a Navent) do segredo de
  *entrada* (que a Navent reenvia para nós) — sem configuração manual no painel.
- **Testes isolados de serviços externos**: `conftest.py` usa um DuckDB temporário
  e zera as credenciais do Trello, garantindo que a suíte nunca crie cards reais.

---

## Testes

```powershell
pip install -r requirements-dev.txt
pytest                      # 23 testes, isolados de serviços externos
```

Cobre a ingestão dos dois portais (health, validação de segredo, mapeamento de
campos, dedup, payload inválido → caixa de revisão + `422`), o cliente da Navent
(montagem do corpo de cadastro do callback) e a carga no Trello (montagem do card,
marcador de rastreio, idempotência).
