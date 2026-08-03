# Environment flavor: grading (all grading tasks)
#
# Grading is static inspection: nothing from the submission or the
# reference solution is ever executed, so this image carries only
# document-reading tools — file/JSON inspection, PDF text extraction,
# spreadsheet and tabular reading, notebook parsing, and Word-document
# reading (pandoc, python-docx) — not a scientific stack. The reading
# tools are preinstalled so the grader's run-time install allowance
# stays the rare exception, not a per-trial network dependency.

FROM python:3.12.11-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        file \
        jq \
        pandoc \
        poppler-utils \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN pip install \
        pandas==2.3.1 \
        openpyxl==3.1.5 \
        pypdf==5.7.0 \
        nbformat==5.10.4 \
        python-docx==1.2.0

# Preinstalled agent runtime: pinned Node and Codex, so Harbor's
# agent-install step finds `codex` on PATH and becomes a no-op. This
# removes the per-trial network install (whose remote Node lookup can
# fail a trial mid-run) and pins the agent version into the image bytes
# instead of letting each trial resolve `@latest`. ripgrep above is
# what that install step would have added alongside. linux-x64: images
# are built and run on x86_64.
ENV NODE_VERSION=22.23.2 \
    CODEX_VERSION=0.146.0
RUN curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" \
    && grep " node-v${NODE_VERSION}-linux-x64.tar.gz\$" SHASUMS256.txt | sha256sum -c - \
    && tar -xzf "node-v${NODE_VERSION}-linux-x64.tar.gz" -C /usr/local --strip-components=1 --no-same-owner \
    && rm "node-v${NODE_VERSION}-linux-x64.tar.gz" SHASUMS256.txt \
    && npm install -g "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && node --version \
    && codex --version

# The grading task layout presents an empty output directory the grader
# must fill (docs/design.md, "Grading task layout").
RUN mkdir -p /app/grading_output

WORKDIR /app
