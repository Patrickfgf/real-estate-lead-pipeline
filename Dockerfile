# Projeto Jaré — imagem de produção da API de ingestão (FastAPI).
# Empacota SÓ o core de runtime (requirements.txt enxuto): sem dashboard, sem
# testes, sem dev. O .dockerignore garante que .venv/, data/, _private/ etc. fiquem de fora.
FROM python:3.11-slim-bookworm

# Boas práticas de imagem Python:
# - PYTHONDONTWRITEBYTECODE: não gera .pyc (imagem mais limpa)
# - PYTHONUNBUFFERED: stdout/stderr sem buffer → logs aparecem na hora (crucial em prod)
# - PIP_NO_CACHE_DIR: não guarda cache do pip (imagem menor)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# gosu: usado pelo ENTRYPOINT para ajustar a permissão do volume (montado como root)
# e então dropar privilégio para o usuário não-root 'app'. UID fixo (10001) por
# previsibilidade da propriedade dos arquivos no volume persistente.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

WORKDIR /app

# Camada de dependências separada do código: muda raramente, então o cache de
# layer do Docker reaproveita o `pip install` enquanto só o código de src/ muda.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Só o pacote da API entra na imagem.
COPY src/ ./src/

# Ponto de montagem do volume persistente (DuckDB). O mount real vem do fly.toml.
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

EXPOSE 8000

# Healthcheck nativo do Docker (o Fly também checa via fly.toml). Deep check: /health
# toca o DuckDB e devolve 503 se o banco cair — não é um 200 estático.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"]

# ENTRYPOINT roda como root SÓ para ajustar a permissão do volume (que o Fly monta
# como root) e então executa o CMD já como o usuário não-root 'app' via gosu.
# Inline (sem script .sh) para evitar problema de CRLF ao versionar no Windows.
ENTRYPOINT ["/bin/sh", "-c", "chown -R app:app /data 2>/dev/null || true; exec gosu app \"$@\"", "--"]

# 1 worker DE PROPÓSITO: o DuckDB é single-writer (conexão singleton + threading.Lock
# por processo). Vários workers = vários processos abrindo o mesmo arquivo .duckdb =
# conflito de lock. No volume do projeto (poucos leads/dia) 1 worker sobra.
# Sem --reload: aquilo é só para desenvolvimento (start.py).
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
