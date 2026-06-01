# Projeto Jaré

Pipeline de dados que ingere leads de portais imobiliários (**Wimóveis** via webhook
oficial e **DFImóveis** via API oficial), limpa, deduplica e enriquece os dados,
armazena em **DuckDB**, pontua a qualidade do lead e carrega no **Trello** + um
**dashboard**, orquestrado num servidor.

**Stack:** Python · FastAPI · DuckDB · pandas · Streamlit · Trello API

## Arquitetura

```
Wimóveis (webhook) ─┐
                    ├─► FastAPI ─► validação (Pydantic) ─► DuckDB ─► limpeza/dedup ─► lead scoring ─► Trello + dashboard
DFImóveis (polling) ─┘
```

## Status

- [x] **Fase 0** — esqueleto do projeto
- [x] **Fase 1** — ingestão Wimóveis (webhook FastAPI → DuckDB com dedup) + testes
- [ ] **Fase 1b** — ingestão DFImóveis (polling com token)
- [ ] **Fase 2** — limpeza / dedup / enriquecimento
- [ ] **Fase 3** — lead scoring
- [ ] **Fase 4** — carga no Trello
- [ ] **Fase 5** — dashboard Streamlit
- [ ] **Fase 6** — orquestração / deploy

## Como rodar (local)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (use source .venv/bin/activate no Linux/Mac)
pip install -r requirements.txt

copy .env.example .env          # Windows (cp no Linux/Mac) e preencher os valores
python start.py
```

- Docs interativas (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Testar o webhook do Wimóveis

Com o servidor no ar, simule a chegada de um lead (PowerShell):

```powershell
$secret = "<WIMOVEIS_WEBHOOK_SECRET>"
Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/webhook/wimoveis?token=$secret" `
  -ContentType "application/json" -InFile "samples\wimoveis_lead.json"
```

Resposta esperada: `{ "status": "received", "external_id": "...", "duplicate": false }`.
Reenviar o mesmo `ExternalId` devolve `"duplicate": true` (dedup funcionando).

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```
