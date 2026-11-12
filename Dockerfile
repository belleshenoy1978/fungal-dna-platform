# Fungal DNA Analysis Platform - Cloud Run image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    VSEARCH_BIN=/usr/local/bin/vsearch \
    UNITE_SINTAX_DB=/data/ref/unite_sintax.fasta.gz \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates tar gzip bzip2 \
    && rm -rf /var/lib/apt/lists/*

# VSEARCH 2.32.0 (Rognes et al. 2016), static Linux x86_64 build
ARG VSEARCH_VERSION=2.32.0
RUN curl -fsSL -o /tmp/vsearch.tgz \
      https://github.com/torognes/vsearch/releases/download/v${VSEARCH_VERSION}/vsearch-${VSEARCH_VERSION}-linux-x86_64.tar.gz \
    && tar -xzf /tmp/vsearch.tgz -C /tmp \
    && install -m 0755 /tmp/vsearch-${VSEARCH_VERSION}-linux-x86_64/bin/vsearch /usr/local/bin/vsearch \
    && rm -rf /tmp/vsearch* \
    && vsearch --version

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p /data/ref /data/jobs /data/results /data/ledger

# The UNITE reference (50 MB download, ~20 MB SINTAX gz) is fetched at container
# start by scripts/entrypoint.sh if it is not already on the mounted /data volume.
EXPOSE 8080
ENTRYPOINT ["/bin/sh", "scripts/entrypoint.sh"]
