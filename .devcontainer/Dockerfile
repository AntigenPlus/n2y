# Dev container for n2y (Antigen Plus fork).
#
# The toolchain deliberately mirrors upstream CI (.github/workflows/tests.yml):
# Python 3.11, pandoc 2.19.2, mermaid-cli 9.4.0. The pandoc *binary* version is
# load-bearing: the `pandoc` Python wrapper only speaks specific pandoc-types
# versions, and n2y's AST code predates the pandoc 3.x AST changes.
FROM mcr.microsoft.com/devcontainers/python:3.11-bookworm

ARG PANDOC_VERSION=2.19.2
ARG MERMAID_CLI_VERSION=9.4.0
ARG BEADS_VERSION=1.2.2

# Node 18 + npm from Debian bookworm (new enough for Claude Code, old enough
# for mermaid-cli 9.4.0's puppeteer). Chromium is installed from apt because
# puppeteer does not ship linux-arm64 Chromium builds; the env vars below make
# puppeteer use the system browser instead of downloading its own.
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y --no-install-recommends \
        nodejs npm \
        chromium \
        fonts-liberation fonts-noto-color-emoji \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# pandoc 2.19.2 (arch-aware: arm64 on Apple Silicon, amd64 elsewhere)
RUN arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/pandoc.deb \
        "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${arch}.deb" \
    && dpkg -i /tmp/pandoc.deb \
    && rm /tmp/pandoc.deb

# mermaid-cli, pinned to match CI
RUN npm install -g "@mermaid-js/mermaid-cli@${MERMAID_CLI_VERSION}"

# beads (bd) for issue tracking
RUN arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/beads.tar.gz \
        "https://github.com/gastownhall/beads/releases/download/v${BEADS_VERSION}/beads_${BEADS_VERSION}_linux_${arch}.tar.gz" \
    && tar -xzf /tmp/beads.tar.gz -C /tmp \
    && install -m 755 /tmp/bd /usr/local/bin/bd \
    && rm -rf /tmp/beads.tar.gz /tmp/bd
