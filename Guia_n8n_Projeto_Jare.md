# Projeto Jaré — Guia de desenvolvimento (n8n self-hosted)

> Integração: **Wimoveis (webhook) + DFImóveis (polling) → Trello**
> Stack: **n8n + PostgreSQL + Caddy** rodando em **VPS Hetzner CX22 (~€4/mês)**
> Público deste documento: você (desenvolvedor). O cliente recebe um manual de uso simplificado depois.

---

## 1. Visão geral da arquitetura

```
┌─────────────┐  webhook POST    ┌───────────────────────────┐
│  Wimoveis   │ ───────────────► │                           │
└─────────────┘                  │                           │     ┌──────────┐
                                 │      n8n (workflow)       │ ──► │  Trello  │
┌─────────────┐  GET a cada 5min │                           │     │  (board) │
│ DFImóveis   │ ◄─────────────── │                           │     └──────────┘
└─────────────┘                  └───────────────────────────┘
                                          │
                                          ▼
                                 ┌───────────────────┐
                                 │ PostgreSQL (n8n)  │  ← estado de execução,
                                 └───────────────────┘     credenciais cripto,
                                                            histórico
```

**Por que assim:**
- Wimoveis empurra (webhook) → custo zero, latência baixa.
- DFImóveis exige polling → cron interno do n8n, custo zero por execução.
- n8n centraliza retry, log, deduplicação, transformação de campos.
- Postgres é obrigatório pra produção (SQLite trava em concorrência).
- Caddy resolve HTTPS automaticamente via Let's Encrypt.

---

## 2. Pré-requisitos — o que você precisa antes de codar

### 2.1 Contas e credenciais a obter

| Item | De quem pedir | Tempo médio |
|------|---------------|-------------|
| Token Wimoveis + ativação de webhook | `atendimento@imovelweb.com.br` (use `email_wimoveis.txt` do projeto) | 3–10 dias úteis |
| Credenciais API DFImóveis (token + endpoint) | Suporte do DFImóveis (use `email_dfimoveis.txt`) | 5–15 dias úteis |
| **Trello: API Key + Token** | Você gera em https://trello.com/power-ups/admin → "New" → depois "Token" | 10 min |
| **Trello: Board ID + List ID "Novos leads"** | Abre o board, adiciona `.json` na URL, copia os IDs | 5 min |
| **Domínio** (ex.: `n8n.corretora.com.br`) | Cliente compra em Registro.br ou cede subdomínio existente | 1 dia |
| **VPS** | Hetzner CX22 Ubuntu 24.04 | 10 min |

### 2.2 O que pedir formalmente ao cliente (lista única)

1. Acesso de admin no painel Wimoveis e DFImóveis (pra ele ativar o webhook/liberar o token quando o suporte pedir).
2. Conta Trello onde o board do operacional vive (ou autorização de criar uma).
3. Um e-mail de notificação pra alertas de falha (`leads@corretora.com.br`).
4. Subdomínio para o n8n (sugerir `automacao.corretora.com.br`).
5. Decisão: a corretora vai ter **um único board** ou **um board por corretor**? (impacta o desenho do fluxo final — começa com um único)

---

## 3. Preparação da VPS (1× setup, ~30 min)

### 3.1 Criar VPS
- Hetzner Cloud → criar Server CX22, Ubuntu 24.04, datacenter Falkenstein ou Ashburn.
- Adicionar sua chave SSH.
- Anotar IP público.

### 3.2 Apontar DNS
- No painel do registrador, criar registro `A` para `n8n.corretora.com.br` → IP da VPS.
- Aguardar propagação (`dig n8n.corretora.com.br` deve responder o IP).

### 3.3 Hardening básico
```bash
ssh root@IP_DA_VPS

# Atualizar
apt update && apt upgrade -y

# Criar usuário não-root
adduser jare
usermod -aG sudo jare
rsync --archive --chown=jare:jare ~/.ssh /home/jare

# Firewall
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable

# Desabilitar login root via SSH
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
```

### 3.4 Instalar Docker
```bash
ssh jare@IP_DA_VPS

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit  # sair e logar de novo pra o grupo valer
```

### 3.5 Subir o n8n
```bash
mkdir -p ~/jare && cd ~/jare

# Copiar para a VPS (do seu Windows, via scp ou WinSCP):
#   docker-compose.yml
#   .env.example  →  renomear para .env e preencher
#   Caddyfile     →  trocar o domínio

# Gerar a chave de criptografia
openssl rand -hex 32   # cole no N8N_ENCRYPTION_KEY do .env

# Subir
docker compose up -d
docker compose logs -f n8n
```

Acesse `https://n8n.corretora.com.br`, faça o login básico (definido no `.env`), e em seguida crie o usuário admin do n8n.

---

## 4. Configurar credenciais dentro do n8n

No editor, vá em **Credentials → New**. Crie **três** credenciais:

### 4.1 Trello API (nó nativo)
- Tipo: **Trello API**
- API Key e API Token (gerados em https://trello.com/power-ups/admin)

### 4.2 Trello Key+Token (HTTP Query Auth) — usada na busca
- Tipo: **Header/Query Auth → Query Auth**
- Não dá pra passar 2 params em Query Auth nativo. Alternativa: **adicione `key` e `token` direto nos `queryParameters`** do nó "Buscar duplicado no Trello" e remova a credencial. (mais simples, e a key/token não são tão sensíveis quanto credenciais de webhook).

### 4.3 DFImóveis API Token
- Tipo: **HTTP Header Auth** (ou Query Auth, dependendo do que o suporte responder)
- Nome do header: `Authorization` (ou o que eles indicarem)
- Valor: `Bearer SEU_TOKEN`

### 4.4 Variáveis do workflow (n8n → Settings → Variables, plano free também tem via env)

Como o plano Community não tem Variables, defina no `docker-compose.yml` (bloco environment do n8n) ou use o nó **Set** no início. Recomendado: hardcode num **nó "Config" Set** no começo do workflow.

Crie um nó Set logo após cada trigger com:
- `TRELLO_BOARD_ID` = id do board (encontra em `https://trello.com/b/SEU_BOARD/nome.json`)
- `TRELLO_LIST_NOVOS_ID` = id da lista "Novos Leads"

---

## 5. Importar e ajustar o workflow

1. No n8n: **Workflows → Import from File** → seleciona `workflow_jare_n8n.json`.
2. Em cada nó marcado com `REPLACE_*_ID`, reabra e selecione a credencial correta.
3. Substitua `$vars.TRELLO_BOARD_ID` e `$vars.TRELLO_LIST_NOVOS_ID` pelos IDs reais (ou crie o nó Config descrito acima).
4. Ative o workflow (toggle no canto superior direito).
5. Copie a URL do webhook gerada — algo como `https://n8n.corretora.com.br/webhook/wimoveis-lead`.
6. Mande essa URL para o suporte da Wimoveis cadastrar.

---

## 6. Anatomia do fluxo (o que cada nó faz)

```
Webhook Wimoveis  ──┬──► Responder 200 OK (Wimoveis)    ← responde rápido pra Wimoveis
                    └──► Normalizar (Wimoveis)
                                  │
Schedule (5 min) ──► GET DFImóveis ──► Separar leads ──► Normalizar (DFImóveis)
                                                                      │
                              ┌───────────────────────────────────────┘
                              ▼
                        Unificar fontes ──► Buscar duplicado no Trello
                                                       │
                                                       ▼
                                                 É novo? ── não ──► Skip
                                                       │
                                                      sim
                                                       ▼
                                              Criar card no Trello
```

### Decisões de design importantes

- **Resposta 200 antes do trabalho:** webhook do Wimoveis tem timeout. Respondemos OK imediato e processamos em paralelo. Se algo der errado, é problema nosso — não da Wimoveis.
- **Normalizar em schema único** (`source`, `externalId`, `leadName`, `leadEmail`, `leadPhone`, `message`, `imovelRef`, `brokerEmail`) — facilita ter um só caminho pra criação do card.
- **Dedup via search no próprio Trello:** cada card termina com `jare-ext:<ID>` na descrição. Antes de criar, fazemos `GET /1/search?query=jare-ext:<ID>`. Se já existe, pula.
  - **Por que não banco:** zero infra extra. Funciona com até ~milhares de leads.
  - **Limitação:** o índice do Trello demora ~30s pra atualizar. Em rajadas muito rápidas pode duplicar — improvável em corretora real, mas anote.
- **Polling 5 min:** equilíbrio entre frescor e custo de chamada. Aumente pra 10–15 min se a API do DFImóveis cobrar/limitar.

---

## 7. Tratamento de erro

No n8n: **Workflows → New → "Jaré - Error Handler"**.

```
[Error Trigger] → [Set: formatar mensagem] → [Send Email / Telegram / Slack]
```

Depois, no workflow principal: **Settings → Error Workflow → "Jaré - Error Handler"**.

Recomendo Telegram (gratuito, instantâneo):
- Criar bot via `@BotFather`
- Pegar `chat_id` enviando msg pro bot e abrindo `https://api.telegram.org/bot<TOKEN>/getUpdates`
- Nó **Telegram → Send Message** no error handler

---

## 8. Testes antes de entregar

### 8.1 Webhook Wimoveis (simular sem a Wimoveis ainda ativa)
```bash
curl -X POST https://n8n.corretora.com.br/webhook/wimoveis-lead \
  -H "Content-Type: application/json" \
  -d '{
    "LeadName": "Teste Patrick",
    "LeadEmail": "teste@exemplo.com",
    "LeadTelephone": "+5561999999999",
    "Message": "Quero visitar o apto",
    "ExternalId": "TST-001",
    "PropertyExternalId": "IMV-12345",
    "BrokerEmail": "corretor@corretora.com.br"
  }'
```

Esperado:
- Card aparece no Trello.
- Rodar o mesmo curl 2× → segunda vez deve cair no "Skip (duplicado)".

### 8.2 Polling DFImóveis
- Aciona manualmente o trigger "Schedule DFImóveis" no editor.
- Se a API ainda não estiver liberada, mocka:
  - Substitua temporariamente o nó "GET DFImóveis API" por um nó **Set** que devolva `{ "leads": [ { "id": "DF-1", "nome": "Fulano", ... } ] }`.

### 8.3 Checklist de aceite
- [ ] 10 leads via webhook → 10 cards no Trello.
- [ ] Mesmo lead enviado 3× → 1 card só.
- [ ] Polling roda a cada 5 min sem erro (deixa rodando 1 hora e olha Executions).
- [ ] Forçar erro (token Trello errado) → notificação chega no Telegram/email.
- [ ] `docker compose restart` → workflow volta ativo automaticamente.

---

## 9. Operação contínua

### 9.1 Backups
```bash
# Backup diário do Postgres + volumes (rodar via cron na própria VPS)
0 3 * * * cd /home/jare/jare && docker compose exec -T postgres pg_dump -U n8n n8n | gzip > ~/backups/n8n_$(date +\%F).sql.gz
```
Combine com `rclone` pra mandar pro Google Drive/B2/S3 — instrução de 1 linha.

### 9.2 Atualização do n8n
```bash
cd ~/jare
docker compose pull
docker compose up -d
```
Faça mensalmente. Antes, sempre backup.

### 9.3 Monitoramento mínimo
- **Uptime Robot** (free): pinga `https://n8n.corretora.com.br/healthz` a cada 5 min.
- **n8n built-in:** menu Executions mostra falhas em vermelho.

---

## 10. Estrutura de cobrança sugerida

| Item | Faixa | Notas |
|------|-------|-------|
| Setup (1×) | R$ 4.500 – 7.000 | Inclui pedido aos suportes, VPS, deploy, testes, treinamento |
| Manutenção mensal | R$ 400 – 600 | VPS (~R$ 30) + backups + updates + suporte por WhatsApp |
| Hora avulsa (mudanças) | R$ 180 – 250 | Mudança de board, novo corretor, nova regra |

Apresenta como **3 pacotes** (Essencial / Profissional / Premium) — deixa o cliente escolher quanto investir.

---

## 11. Próximos passos práticos

1. ⏭️ Enviar `email_wimoveis.txt` e `email_dfimoveis.txt` para os suportes — **isso trava o cronograma**, faça hoje.
2. ⏭️ Comprar VPS Hetzner + cadastrar domínio (1h de trabalho).
3. ⏭️ Subir o stack (`docker compose up -d`) e validar acesso HTTPS.
4. ⏭️ Importar `workflow_jare_n8n.json`, configurar credenciais, rodar testes do item 8.
5. ⏭️ Apresentar pro cliente com leads reais já caindo.

---

## 12. Referências úteis

- Docs Wimoveis Lead Manager: https://developers.grupozap.com/leadManager/
- API Trello: https://developer.atlassian.com/cloud/trello/rest/
- Docs n8n self-hosted: https://docs.n8n.io/hosting/
- Hetzner Cloud: https://www.hetzner.com/cloud/
- Caddy (HTTPS automático): https://caddyserver.com/docs/
