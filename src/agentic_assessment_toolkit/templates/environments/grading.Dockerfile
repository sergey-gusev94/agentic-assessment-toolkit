# Environment flavor: grading (all grading tasks)
#
# Grading is static inspection: nothing from the submission or the
# reference solution is ever executed. This image carries
# document-reading tools — file/JSON inspection, PDF text extraction
# and OCR for scanned pages, image inspection and cropping, PDF
# structure inspection, spreadsheet and tabular reading, notebook
# parsing, Word and PowerPoint reading, archive unpacking, text
# extraction from binaries — plus scipy, scikit-learn, and sympy for
# the grader's own independent check calculations (its code on its own
# inputs, which the grader prompt sanctions). Everything is
# preinstalled so the grader's run-time install allowance stays the
# rare exception, not a per-trial network dependency.
#
# ImageMagick keeps Debian's default security policy, which disables
# its Ghostscript-based PDF conversion: graders rasterize untrusted
# PDFs with poppler's pdftoppm instead, so the Ghostscript attack
# surface stays closed.

FROM python:3.12.11-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils \
        ca-certificates \
        curl \
        file \
        imagemagick \
        jq \
        pandoc \
        poppler-data \
        poppler-utils \
        qpdf \
        ripgrep \
        tesseract-ocr \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN pip install \
        pandas==2.3.1 \
        scipy==1.16.1 \
        scikit-learn==1.7.1 \
        sympy==1.14.0 \
        openpyxl==3.1.5 \
        pypdf==5.7.0 \
        nbformat==5.10.4 \
        python-docx==1.2.0 \
        python-pptx==1.0.2

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
