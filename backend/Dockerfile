FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEVDNA_DATA_DIR=/data \
    PORT=8000

WORKDIR /srv

COPY requirements.lock requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt \
    && python -c "from tree_sitter_language_pack import get_parser; [get_parser(l) for l in ('javascript', 'typescript', 'tsx')]"

COPY app ./app
COPY sample_project ./sample_project

RUN useradd --create-home --uid 10001 devdna \
    && mkdir -p /data \
    && chown -R devdna:devdna /srv /data

USER devdna

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=3)" || exit 1

# Honors Cloud Run's injected $PORT (default 8000 locally).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
