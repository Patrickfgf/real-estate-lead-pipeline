"""Sobe o servidor de ingestão do Projeto Jaré.

Uso:  python start.py
"""
import uvicorn

from src.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )
