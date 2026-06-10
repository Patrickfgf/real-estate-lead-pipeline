# Deploy

Este projeto tem **dois deploys independentes**:

- **API de ingestão** (`src/main.py`) — o serviço de produção que recebe os webhooks dos
  portais e grava no DuckDB. Roda sempre no ar, com **dado real**, privado no Fly.io.
  Ver [API de ingestão de leads (produção — Fly.io)](#api-de-ingestão-de-leads-produção--flyio).
- **Dashboard** (`dashboard/app.py`) — add-on de *Data Analysis* sobre a camada curada,
  publicado como **demo pública com dados sintéticos**. As opções A/B/C abaixo são desse
  deploy.

## Dashboard (demo pública)

O dashboard (`dashboard/app.py`) é o add-on de *Data Analysis* sobre a camada curada.
Esta é a **demo pública com dados sintéticos** — o app **gera os dados sozinho** na 1ª
execução (auto-seed), então não precisa subir banco nenhum.

> ⚠️ **Dado real de cliente nunca vai para uma URL pública** (LGPD). Esta hospedagem é
> só para a demo com dados fictícios. A versão de produção (leads reais) roda **privada**
> no VPS, atrás de HTTPS + autenticação (Fase 6) — ver o final deste arquivo.

### Opção A — Streamlit Community Cloud (grátis, recomendado para o portfólio)

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

### Opção B — Hugging Face Spaces (grátis, bem-vista em DS/ML)

1. Crie um **Space** com SDK **Streamlit**.
2. Aponte o app para `dashboard/app.py` e use o conteúdo do `dashboard/requirements.txt`
   como `requirements.txt` do Space (o Space é o seu próprio repositório git).

### Opção C — Demo rápida via túnel (sem hospedar nada)

Para mostrar **agora**, sem deploy:

```bash
streamlit run dashboard/app.py
cloudflared tunnel --url http://localhost:8501
```

Mande a URL pública gerada (`*.trycloudflare.com`). É efêmera: vive enquanto a sua
máquina e os dois processos ficarem de pé.

## API de ingestão de leads (produção — Fly.io)

A API (`src/main.py`) recebe os webhooks dos portais (Wimóveis/Navent e DFImóveis) e
grava no DuckDB. Diferente do dashboard, ela roda **sempre no ar** (webhook não pode
dormir) e com **dado real** — então fica numa conta privada do Fly, com os segredos
**fora do repositório** (via `fly secrets`, nunca no `fly.toml`, que é versionado).

A região é **`gru`** (São Paulo): menor latência para os portais brasileiros e dado de
lead mantido no Brasil (LGPD).

### Pré-requisitos

- Conta no [Fly.io](https://fly.io) **do cliente** (dono da infra) com cartão cadastrado.
- `flyctl` instalado — ver <https://fly.io/docs/flyctl/install/>.
- As credenciais do Trello e os segredos de webhook (gerados no passo 4).

### Passo a passo

1. **Login** (abre o navegador):

   ```bash
   fly auth login
   ```

2. **Cria o app sem deployar ainda.** `--ha=false` garante **uma máquina só** — o DuckDB
   é *single-writer* e só há um volume; duas máquinas causariam *split-brain* de dados.
   O nome precisa ser único no Fly:

   ```bash
   fly launch --no-deploy --ha=false --name jare-leads-api --region gru
   ```

   Se o nome estiver tomado, escolha outro e ajuste o `app =` no `fly.toml`.

3. **Cria o volume persistente ANTES do deploy.** Sem ele, o `[mounts]` do `fly.toml` faz
   o `fly deploy` **falhar**; e sem volume os leads sumiriam a cada redeploy/restart:

   ```bash
   fly volumes create jare_data --region gru --size 1
   ```

4. **Gera os dois segredos de webhook** (um por portal):

   ```bash
   openssl rand -hex 32   # → use como WIMOVEIS_WEBHOOK_SECRET
   openssl rand -hex 32   # → use como DFIMOVEIS_WEBHOOK_SECRET
   ```

5. **Seta os segredos** (nunca no `fly.toml`). Os valores do Trello estão no seu `.env`
   local. **Obrigatórios** para o app subir funcional (recepção + carga no Trello):

   ```bash
   fly secrets set \
     WIMOVEIS_WEBHOOK_SECRET=<gerado-no-passo-4> \
     DFIMOVEIS_WEBHOOK_SECRET=<gerado-no-passo-4> \
     TRELLO_API_KEY=... \
     TRELLO_API_TOKEN=... \
     TRELLO_LIST_ID=... \
     TRELLO_LABEL_WIMOVEIS=... \
     TRELLO_LABEL_DFIMOVEIS=... \
     TRELLO_LABEL_QUENTE=... \
     TRELLO_LABEL_MORNO=... \
     TRELLO_LABEL_FRIO=...
   ```

   > ⚠️ **Sem os `*_WEBHOOK_SECRET` o webhook sobe em modo ABERTO** — qualquer um na
   > internet poderia injetar leads falsos (que virariam cards no Trello). O app emite um
   > WARNING no log do boot se subir sem segredo, mas a proteção é setar o segredo aqui.

   **Opcionais (só quando a Navent liberar produção):** `NAVENT_BASE_URL`
   (`https://api-br-open.navent.com`), `NAVENT_TOKEN`, `NAVENT_USER`, `NAVENT_PASSWORD`.
   O `WEBHOOK_PUBLIC_URL` é setado no passo 7, depois que a URL existe.

6. **Deploy:**

   ```bash
   fly deploy
   ```

7. **Valida o healthcheck** (deep check: 200 só se o DuckDB responde) e **registra a URL
   pública** do próprio app:

   ```bash
   curl https://jare-leads-api.fly.dev/health
   # {"status":"ok","db":"ok"}

   fly secrets set WEBHOOK_PUBLIC_URL=https://jare-leads-api.fly.dev
   ```

8. **Endpoints dos portais** (informe a cada portal no cadastro do callback):

   - **Wimóveis/Navent:** `POST https://jare-leads-api.fly.dev/webhook/wimoveis`
     — segredo no header `x-webhook-token` = `WIMOVEIS_WEBHOOK_SECRET`.
   - **DFImóveis (VrSync):** `POST https://jare-leads-api.fly.dev/webhook/dfimoveis?token=<DFIMOVEIS_WEBHOOK_SECRET>`.

### Operação

- **Logs em tempo real:** `fly logs`
- **Garantir uma máquina só** (DuckDB single-writer): `fly scale count 1`
- **Mais memória** se faltar (pandas no rebuild da camada curada): `fly scale memory 1024`
- **Caixa de revisão** (leads que falharam a validação, nada se perde):
  `fly ssh console -C "python -m src.db dead-letter"`

## Dashboard com dado real (privado)

O dashboard com **leads reais** nunca vai para Community Cloud / Spaces — dado de cliente
fica privado. Rode-o ao lado da API (no Fly, atrás de autenticação, ou num VPS com
HTTPS + senha), lendo o `leads_clean` **real** que o pipeline grava.
