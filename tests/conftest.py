"""Configuração compartilhada dos testes.

Define as variáveis de ambiente ANTES de qualquer import de `src` — como o
`src.config` lê o ambiente no momento do import, isso garante que os testes
usem um banco DuckDB temporário e um segredo de webhook conhecido, sem tocar
no banco real nem exigir um .env.
"""
import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="jare_test_")
os.environ["DUCKDB_PATH"] = os.path.join(_TMP_DIR, "test.duckdb")
os.environ["WIMOVEIS_WEBHOOK_SECRET"] = "segredo-de-teste"
