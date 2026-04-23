FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "python-frontmatter>=1.1" \
    "pydantic>=2.9" \
    "httpx>=0.27" \
    "jinja2>=3.1"

COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts

ENV PYTHONPATH=/app/backend
ENV OLLAMA_URL=http://host.docker.internal:11434
ENV OLLAMA_MODEL=llama3.1:8b
ENV CODEX_ROOT=/app/codex
ENV CODEX_DB=/app/data/codex.db

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
