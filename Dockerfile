# Imagem de produção da API de ingestão (FastAPI + DuckDB).
# Só o core entra (requirements.txt da raiz) — o dashboard é um add-on à parte.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Camada de dependências separada do código: rebuild de deploy não reinstala tudo.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

# Em produção o banco fica no volume montado em /data (ver fly.toml). O default
# relativo (data/jare.duckdb) só vale em dev local.
ENV DUCKDB_PATH=/data/jare.duckdb

EXPOSE 8000

# 1 worker é REQUISITO, não economia: a idempotência da carga no Trello e a
# serialização de escrita do DuckDB usam locks de processo (src/trello.py e
# src/db.py). Multiplicar workers fura a garantia "uma pessoa, um card".
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
