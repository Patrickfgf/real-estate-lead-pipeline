# Deploy do dashboard (demo pública)

O dashboard (`dashboard/app.py`) é o add-on de *Data Analysis* sobre a camada curada.
Esta é a **demo pública com dados sintéticos** — o app **gera os dados sozinho** na 1ª
execução (auto-seed), então não precisa subir banco nenhum.

> ⚠️ **Dado real de cliente nunca vai para uma URL pública** (LGPD). Esta hospedagem é
> só para a demo com dados fictícios. A versão de produção (leads reais) roda **privada**
> no VPS, atrás de HTTPS + autenticação (Fase 6) — ver o final deste arquivo.

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

## Produção com leads reais (Fase 6)

No VPS, junto da API de ingestão: Streamlit atrás do **Caddy (HTTPS) + autenticação**,
lendo o `leads_clean` **real** que o pipeline grava. Nunca em Community Cloud / Spaces —
dado real de cliente fica privado.
