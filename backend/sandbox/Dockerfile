# Base image for running experiments on untrusted projects.
# The runner generates a requirements.txt from the project manifest/lockfile and
# builds a temporary image from this Dockerfile before starting an isolated run.
FROM python:3.12-slim
RUN pip install --no-cache-dir "pytest>=8.3"
COPY requirements.txt /tmp/devdna-requirements.txt
RUN if [ -s /tmp/devdna-requirements.txt ]; then \
      pip install --no-cache-dir -r /tmp/devdna-requirements.txt; \
    fi \
    && useradd --create-home --uid 10001 sandbox
USER sandbox
WORKDIR /src
