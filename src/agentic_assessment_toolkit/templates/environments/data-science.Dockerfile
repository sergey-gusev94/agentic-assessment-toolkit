# Environment flavor: data-science (solve tasks)
#
# Baseline for data-science coursework: numpy/pandas/matplotlib,
# scikit-learn, CPU-only PyTorch, spreadsheet reading, the notebook
# toolchain, PDF text extraction (handouts are routinely PDFs), and
# basic file/JSON inspection utilities.
# Package versions are pinned (docs/design.md, "Environment templates");
# base-image digest pinning is deferred to reportable runs.

FROM python:3.12.11-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        file \
        fonts-dejavu-core \
        git \
        jq \
        pandoc \
        poppler-data \
        poppler-utils \
        ripgrep \
        tesseract-ocr \
        unzip \
    && rm -rf /var/lib/apt/lists/*

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

RUN pip install \
        numpy==2.3.2 \
        pandas==2.3.1 \
        scipy==1.16.1 \
        matplotlib==3.10.3 \
        scikit-learn==1.7.1 \
        openpyxl==3.1.5 \
        pypdf==5.7.0 \
        ipykernel==6.29.5 \
        nbclient==0.10.2 \
        nbconvert==7.16.6 \
        python-docx==1.2.0 \
        python-pptx==1.0.2

# CPU-only PyTorch: the coursework needs no GPU and the CPU wheel is
# far smaller than the default CUDA build.
RUN pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu

WORKDIR /app
