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
os.environ["DFIMOVEIS_WEBHOOK_SECRET"] = "segredo-de-teste"
os.environ["ADMIN_TOKEN"] = "admin-de-teste"

# Isola os testes de serviços externos: SEM credenciais de Trello, a ingestão
# não dispara carga real (o webhook só empurra pro Trello se houver api_key +
# list_id). Como load_dotenv() não sobrescreve o ambiente já definido, isto
# vence o que estiver no .env e evita criar cards reais durante os testes.
os.environ["TRELLO_API_KEY"] = ""
os.environ["TRELLO_API_TOKEN"] = ""
os.environ["TRELLO_LIST_ID"] = ""
os.environ["TRELLO_LABEL_WIMOVEIS"] = ""
os.environ["TRELLO_LABEL_DFIMOVEIS"] = ""
