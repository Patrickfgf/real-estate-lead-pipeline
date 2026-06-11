# Deploy

Este arquivo cobre **dois deploys diferentes** — não confundir:

1. **Produção (leads reais)** — a API de ingestão (FastAPI + DuckDB + Trello) no
   **Fly.io**. É o que recebe os webhooks dos portais. [Ver abaixo ↓](#produção-no-flyio-api-de-ingestão-com-leads-reais)
2. **Demo pública (dados sintéticos)** — o dashboard Streamlit no Community Cloud,
   para o portfólio. [Ver no final ↓](#deploy-do-dashboard-demo-pública)

---

## Produção no Fly.io (API de ingestão, com leads reais)

Artefatos: `Dockerfile`, `.dockerignore` e `fly.toml` (raiz do repo). A imagem
container só leva `src/` + `requirements.txt`; o banco DuckDB vive num **volume
persistente** montado em `/data`. **Uma máquina só, sempre ligada** — o DuckDB é
single-writer e os portais entregam lead por push (máquina dormindo = lead em risco).

### Passo a passo (1ª vez)

```bash
# 0. Login na conta Fly.io onde o cartão do cliente está cadastrado
fly auth login

# 1. Criar o app (nome do fly.toml) e o volume do banco na mesma região
fly apps create jare-leads
fly volumes create jare_data --app jare-leads --region gru --size 1 --yes

# 2. Segredos dos webhooks + token de operação. CAPTURAR os valores em variáveis
#    (o Fly não devolve segredo em texto puro depois!): o registro nos portais
#    exige os valores literais — o `navent register` envia o WIMOVEIS_* do .env
#    LOCAL (tem que ser IDÊNTICO ao do Fly) e a URL da DFImóveis embute o DFI_*.
#    Guarde os três no .env local (gitignored) e/ou num cofre.
WIM=$(openssl rand -hex 32); DFI=$(openssl rand -hex 32); ADM=$(openssl rand -hex 32)
fly secrets set --app jare-leads --stage \
  WIMOVEIS_WEBHOOK_SECRET="$WIM" \
  DFIMOVEIS_WEBHOOK_SECRET="$DFI" \
  ADMIN_TOKEN="$ADM"
# (perdeu os valores? recupere do ambiente da máquina:
#  fly ssh console --app jare-leads -C "printenv WIMOVEIS_WEBHOOK_SECRET")

# 3. Credenciais do Trello do CLIENTE (https://trello.com/app-key) + Navent
fly secrets set --app jare-leads --stage \
  TRELLO_API_KEY=... TRELLO_API_TOKEN=... \
  TRELLO_LIST_ID=... TRELLO_LABEL_WIMOVEIS=... TRELLO_LABEL_DFIMOVEIS=... \
  TRELLO_LABEL_QUENTE=... TRELLO_LABEL_MORNO=... TRELLO_LABEL_FRIO=... \
  NAVENT_BASE_URL=https://api-br-open.navent.com \
  NAVENT_TOKEN=... \
  WEBHOOK_PUBLIC_URL=https://jare-leads.fly.dev
# (IDs de lista/etiquetas saem de: python -m src.trello setup, rodado com o .env real)

# 4. Deploy — SEMPRE com --ha=false (senão o Fly cria 2ª máquina e o volume não acompanha)
fly deploy --ha=false

# 5. Smoke test
curl -s https://jare-leads.fly.dev/health          # → {"status":"ok","db":"ok"}
```

### Registrar os webhooks nos portais (depois do deploy)

- **Wimóveis (Navent):** com `NAVENT_TOKEN` de produção e `WEBHOOK_PUBLIC_URL`
  apontando para o Fly, rodar `python -m src.navent register` (ou `--dry-run`
  antes). O callback fica em `https://jare-leads.fly.dev/webhook/wimoveis`.
- **DFImóveis (VrSync):** cadastro manual com o portal; informar a URL
  `https://jare-leads.fly.dev/webhook/dfimoveis?token=<DFIMOVEIS_WEBHOOK_SECRET>`.

> ⚠️ Os webhooks rodam **fail-open quando o segredo está vazio** (modo dev). Os
> secrets do passo 2 têm que estar no ar **antes** de divulgar as URLs aos portais
> — o boot loga um aviso bem visível se algum estiver faltando.

### Operação do dia a dia

```bash
fly logs --app jare-leads                          # logs ao vivo (lead recebido, dup, falha Trello)
fly status --app jare-leads                        # saúde da máquina/health check

# Caixa de revisão e reenvio ao Trello: SEMPRE via endpoints /admin/* (rodam
# dentro do processo da API). NÃO use `fly ssh console -C "python -m src..."`:
# o DuckDB é single-writer e o uvicorn segura o lock do arquivo — um segundo
# processo falha com "Conflicting lock is held".
curl -s -H "X-Admin-Token: $ADM" https://jare-leads.fly.dev/admin/dead-letter
curl -s -X POST -H "X-Admin-Token: $ADM" https://jare-leads.fly.dev/admin/trello/push
```

- **Backup do banco:** snapshot diário automático do volume (retenção padrão 5
  dias). Restaurar = `fly volumes snapshots list` + criar volume a partir do snapshot.
- **Deploy de atualização:** `fly deploy --ha=false`. Há ~segundos de downtime
  (1 máquina + volume); os portais re-tentam a entrega (a DFImóveis re-tenta 3x e
  guarda por 14 dias), então lead não se perde.

---

# Deploy do dashboard (demo pública)

O dashboard (`dashboard/app.py`) é o add-on de *Data Analysis* sobre a camada curada.
Esta é a **demo pública com dados sintéticos** — o app **gera os dados sozinho** na 1ª
execução (auto-seed), então não precisa subir banco nenhum.

> ⚠️ **Dado real de cliente nunca vai para uma URL pública** (LGPD). Esta hospedagem é
> só para a demo com dados fictícios. Em produção, os leads reais vivem na API do
> Fly.io (seção acima); um dashboard sobre eles (Fase 6) rodaria **privado**, atrás
> de autenticação — ver o final deste arquivo.

## Opção A — Streamlit Community Cloud (grátis, recomendado para o portfólio)

1. Garanta o repositório no GitHub (público ou privado — o Community Cloud acessa os
   dois depois que você autoriza o acesso).
2. Acesse <https://share.streamlit.io> e faça login com o GitHub.
3. **Create app** → escolha o repositório e o branch e, em *Main file path*, informe
   `dashboard/app.py`.
4. **Deploy.** O Community Cloud instala as dependências do **`dashboard/requirements.txt`**
   (ele procura o requirements primeiro no diretório do app, então este tem precedência
   sobre o `requirements.txt` enxuto da raiz). Na 1ª carga o app **gera os dados demo
   automaticamente** (~30s).
5. Pronto: você recebe uma URL fixa tipo `https://<seu-app>.streamlit.app` para colar no
   CV/LinkedIn.

Notas:
- O app "dorme" após um tempo sem acesso e **acorda na 1ª visita** (~30s) — normal no
  plano gratuito.
- **Sem custo, sem cartão de crédito.**

## Opção B — Hugging Face Spaces (grátis, bem-vista em DS/ML)

1. Crie um **Space** com SDK **Streamlit**.
2. Aponte o app para `dashboard/app.py` e use o conteúdo do `dashboard/requirements.txt`
   como `requirements.txt` do Space (o Space é o seu próprio repositório git).

## Opção C — Demo rápida via túnel (sem hospedar nada)

Para mostrar **agora**, sem deploy:

```bash
streamlit run dashboard/app.py
cloudflared tunnel --url http://localhost:8501
```

Mande a URL pública gerada (`*.trycloudflare.com`). É efêmera: vive enquanto a sua
máquina e os dois processos ficarem de pé.

## Dashboard sobre leads reais (Fase 6)

A API de ingestão roda no **Fly.io** (seção no topo deste arquivo). O dashboard de
produção rodaria junto dela — um segundo processo/app no Fly lendo o `leads_clean`
do volume, atrás de **autenticação** — ou em host privado a definir. Nunca em
Community Cloud / Spaces — dado real de cliente fica privado.
