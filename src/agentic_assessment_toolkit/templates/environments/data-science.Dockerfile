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
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

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
        nbconvert==7.16.6

# CPU-only PyTorch: the coursework needs no GPU and the CPU wheel is
# far smaller than the default CUDA build.
RUN pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu

WORKDIR /app
