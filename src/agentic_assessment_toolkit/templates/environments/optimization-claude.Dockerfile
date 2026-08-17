# Environment flavor: optimization (solve tasks)
#
# Baseline for optimization coursework: Pyomo with HiGHS as the
# license-free default solver, Ipopt from conda-forge (it is a native
# binary that pip cannot provide), GLPK, and gurobipy installed but
# unlicensed. scikit-learn and sympy cover the course's clustering and
# symbolic-algebra assignments. A conda-forge base is used because of
# Ipopt.
#
# Gurobi is enabled at run time by mounting gurobi.lic read-only at
# /opt/gurobi/gurobi.lic from outside the repository. Never bake a
# license into this image (docs/data-conventions.md).

FROM mambaorg/micromamba:2.1.1

USER root

# Put the conda env on PATH directly so python and the solvers work
# regardless of how the container entrypoint is invoked.
ENV MAMBA_ROOT_PREFIX=/opt/conda \
    PATH=/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    MPLBACKEND=Agg \
    PIP_NO_CACHE_DIR=1

RUN micromamba install -y -n base -c conda-forge \
        python=3.12.11 \
        pip=25.1.1 \
        file \
        jq \
        pyomo=6.9.2 \
        ipopt=3.14.17 \
        highspy=1.11.0 \
        glpk=5.0 \
        numpy=2.3.2 \
        scipy=1.16.1 \
        pandas=2.3.1 \
        matplotlib=3.10.3 \
        openpyxl=3.1.5 \
        pypdf=5.7.0 \
        scikit-learn=1.7.1 \
        sympy=1.14.0 \
        ipykernel=6.29.5 \
        nbclient=0.10.2 \
        nbconvert=7.16.6 \
        poppler \
        poppler-data \
        pandoc \
        tesseract \
        unzip \
        git \
        curl \
        ripgrep \
    && micromamba clean -a -y

RUN pip install gurobipy==12.0.3 python-docx==1.2.0 python-pptx==1.0.2

# Preinstalled agent runtime: pinned Node and Claude Code, so Harbor's
# agent-install step finds `claude` on PATH and becomes a no-op. This
# removes the per-trial network install (whose remote download can fail
# a trial mid-run) and pins the agent version into the image bytes
# instead of letting each trial resolve the newest release. procps is
# what that install step would have added alongside: Claude Code shells
# out to `ps` and `pgrep` to clean up process trees, and once `claude`
# is on PATH the step that would have installed procps never runs.
# linux-x64: images are built and run on x86_64.
# DISABLE_AUTOUPDATER stops Claude Code from replacing itself inside the
# container: the pinned version above is part of the image bytes, and so
# of every item identity derived from them, which a CLI that updates
# itself mid-run would quietly invalidate. Harbor also sets
# CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1, which suppresses the
# updater today, but that is Harbor's choice and not this pin's
# guarantee.
ENV DISABLE_AUTOUPDATER=1
ENV NODE_VERSION=22.23.2 \
    CLAUDE_CODE_VERSION=2.1.233
RUN apt-get update \
    && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" \
    && grep " node-v${NODE_VERSION}-linux-x64.tar.gz\$" SHASUMS256.txt | sha256sum -c - \
    && tar -xzf "node-v${NODE_VERSION}-linux-x64.tar.gz" -C /usr/local --strip-components=1 --no-same-owner \
    && rm "node-v${NODE_VERSION}-linux-x64.tar.gz" SHASUMS256.txt \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && node --version \
    && claude --version

WORKDIR /app
