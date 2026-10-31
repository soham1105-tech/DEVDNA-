# Base image for running experiments on untrusted projects.
# The runner validates the project's dependency specs with the shared strict
# parser, vendors binary wheels for them on the trusted host (pip download
# --only-binary=:all:), and builds this image with --network=none: the build
# can neither execute untrusted code nor reach the network.
FROM python:3.12-slim
COPY wheels/ /wheels/
COPY requirements.txt /tmp/devdna-requirements.txt
RUN pip install --no-index --find-links=/wheels \
      $(if [ -s /tmp/devdna-requirements.txt ]; then echo "-r /tmp/devdna-requirements.txt"; fi) \
    && useradd --create-home --uid 10001 sandbox
USER sandbox
WORKDIR /src
