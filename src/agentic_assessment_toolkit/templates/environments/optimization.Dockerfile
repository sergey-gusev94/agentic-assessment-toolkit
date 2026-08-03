# Environment flavor: optimization (solve tasks)
#
# Baseline for optimization coursework: Pyomo with HiGHS as the
# license-free default solver, Ipopt from conda-forge (it is a native
# binary that pip cannot provide), GLPK, and gurobipy installed but
# unlicensed. A conda-forge base is used because of Ipopt.
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
        ipykernel=6.29.5 \
        nbclient=0.10.2 \
        nbconvert=7.16.6 \
        poppler \
        git \
        curl \
        ripgrep \
    && micromamba clean -a -y

RUN pip install gurobipy==12.0.3

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

WORKDIR /app
