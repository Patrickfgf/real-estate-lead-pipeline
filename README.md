# Projeto Jaré — Pipeline de Ingestão de Leads Imobiliários

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?logo=pydantic&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?logo=duckdb&logoColor=black)
![Trello API](https://img.shields.io/badge/Trello%20API-0052CC?logo=trello&logoColor=white)
![pytest](https://img.shields.io/badge/tested%20with-pytest-0A9EDC?logo=pytest&logoColor=white)

Pipeline de dados que **ingere leads de portais imobiliários** (**Wimóveis** via
callback oficial da Navent e **DFImóveis** via webhook oficial padrão VrSync),
valida, deduplica e persiste em **DuckDB** (camada crua), **limpa e enriquece**
numa camada curada (pandas), pontua a qualidade do lead e os carrega no **Trello**
(funil de vendas) e num **dashboard** — para que o time comercial atue rápido sem
perder lead. Payloads que não validam não se perdem: vão para uma **caixa de
revisão** (dead-letter) para inspeção e reprocessamento.

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
              limpeza + enriquecimento (pandas) → leads_clean   ✅
                        → lead scoring  [planejado]
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
| Limpeza / enriquecimento | **pandas** | Camada curada (`leads_clean`): normaliza telefone/e-mail, enriquece por DDD (UF/região), garimpa o `raw_payload` e deduplica a mesma pessoa entre portais |
| Integração (saída) | **Trello REST API** (via `requests`) | Cria os cards no funil + "infra como código" do quadro |
| Integração (entrada) | **Navent Open API** (via `requests`) | Cadastra/gerencia o callback de leads do Wimóveis |
| Config | **python-dotenv** | Variáveis de ambiente / segredos fora do código |
| Testes | **pytest** + **httpx** (`TestClient`) | Testes de ingestão, transform e carga, isolados de serviços externos |
| _Planejado (Fases 3 e 5)_ | **Streamlit**, **Plotly** | Dashboard e visualização |

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
| 2 | Limpeza / enriquecimento → camada curada `leads_clean` (pandas) + dedup entre portais | ✅ |
| 3 | Lead scoring (qualidade do lead) | ⬜ |
| 5 | Dashboard Streamlit | ⬜ |
| 6 | Orquestração / deploy | ⬜ |

> A **Fase 4 foi antecipada** para fechar uma fatia vertical — *lead entra → card
> sai no Trello* — e entregar valor cedo. A **Fase 2** (limpeza/enriquecimento) já
> construiu a camada curada `leads_clean`, base analítica do projeto. Os próximos
> saltos são o **scoring** (Fase 3) e o **dashboard** (Fase 5), ambos consumindo a
> camada curada.

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
│   ├── transform.py         # Camada curada: limpeza/enriquecimento (pandas) → leads_clean (CLI)
│   └── main.py              # App FastAPI (monta as rotas + /health + logging)
├── tests/
│   ├── conftest.py          # Isola os testes: DuckDB temporário, sem credenciais reais
│   ├── test_ingest_wimoveis.py
│   ├── test_ingest_dfimoveis.py
│   ├── test_navent.py
│   ├── test_trello.py
│   └── test_transform.py    # Normalização (telefone/e-mail), enriquecimento e dedup
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

## Camada curada — limpeza & enriquecimento (Fase 2)

O projeto separa **camada crua** de **camada curada** (padrão raw → curated):

- **`leads_raw`** — exatamente o que o portal mandou (auditável, intocado).
- **`leads_clean`** — derivada por um *transform* em **pandas**, **reconstruível a
  qualquer momento** a partir da crua (idempotente).

O transform (`src/transform.py`) faz, com funções puras e testadas:

- **Telefone (padrão BR):** tira a máscara, trata DDD e código de país (+55),
  classifica fixo vs. celular e gera o formato **E.164** (`+5561999998888`) — o
  que também serve de chave para o dedup.
- **E-mail:** normaliza (minúsculas/trim), valida o formato e extrai o domínio.
- **Nome:** colapsa espaços e capitaliza, mantendo partículas (`de`/`da`/`do`).
- **Geografia:** do DDD deriva **UF** e **região** (ex.: 61 → DF / Centro-Oeste).
- **Garimpo do `raw_payload`:** extrai campos que não estão no lead canônico —
  tipo de transação (`SELL`→Compra / `RENT`→Aluguel), temperatura do portal
  (sinal para o scoring), origem e *anúncio em destaque* (Wimóveis).
- **Dedup entre portais (entity-resolution-lite):** a mesma pessoa (mesmo
  telefone/e-mail) que entrou no Wimóveis **e** na DFImóveis é agrupada; o lead
  mais antigo vira o **primário** e os demais são marcados como duplicados, com a
  flag `cross_portal`.

```powershell
python -m src.transform   # reconstrói leads_clean a partir de leads_raw + imprime um resumo
```

A camada curada é a base analítica que o **scoring** (Fase 3) e o **dashboard**
(Fase 5) vão consumir. O rebuild completo é barato no volume do projeto (alguns
leads/dia) e mantém a curada sempre coerente com a crua.

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

**`leads_clean`** — camada curada (Fase 2): derivada de `leads_raw` pelo transform, com os campos limpos/enriquecidos:

| Coluna | Descrição |
|---|---|
| `name_clean` | Nome normalizado (capitalização + partículas) |
| `email_clean`, `email_valid`, `email_domain` | E-mail normalizado, validade e domínio |
| `phone_e164`, `phone_ddd`, `phone_is_mobile`, `phone_valid` | Telefone em E.164, DDD, fixo/celular e validade |
| `uf`, `regiao` | Unidade federativa e região, derivadas do DDD |
| `transaction_type`, `portal_temperature`, `lead_origin`, `is_destaque` | Campos garimpados do `raw_payload` |
| `person_key`, `is_primary`, `is_duplicate`, `cross_portal` | Dedup de identidade: chave da pessoa, primário vs. duplicado e se cruza portais |

> É reconstruída inteira a cada `python -m src.transform` — não há estado parcial.

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
- **Camada crua vs. curada (raw → curated)**: `leads_raw` preserva o payload
  exato (auditoria); `leads_clean` é derivada por um transform em **pandas**,
  reconstruível e idempotente. Separa o que foi *recebido* do que foi *tratado* —
  reprocessável sem risco de estado parcial.
- **Dedup de identidade entre portais**: além do dedup técnico por
  `(source, external_id)`, a camada curada resolve a *mesma pessoa* por telefone/
  e-mail normalizados (E.164), marcando duplicados e o cruzamento entre portais
  (entity-resolution-lite) — sem isso, um lead repetido vira dois cards.
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
pytest                      # 38 testes, isolados de serviços externos
```

Cobre a ingestão dos dois portais (health, validação de segredo, mapeamento de
campos, dedup, payload inválido → caixa de revisão + `422`), o cliente da Navent
(montagem do corpo de cadastro do callback), a carga no Trello (montagem do card,
marcador de rastreio, idempotência) e a camada curada (normalização de telefone/
e-mail, enriquecimento por DDD, garimpo do `raw_payload` e dedup entre portais).
