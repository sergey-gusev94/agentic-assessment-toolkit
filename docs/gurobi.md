# Gurobi WLS Setup

Optimization task images include `gurobipy` but no license. Keep `gurobi.lic`
outside this repository and the data root. Provide its host path through the
command line or the `AAT_GUROBI_LICENSE_FILE` environment variable:

```bash
export AAT_GUROBI_LICENSE_FILE=/home/user/gurobi.lic

aat solve --course PU_CHE597CO_S2026 \
  --config codex-high --max-concurrent-trials 1
```

AAT verifies that the path is a file and that every selected assignment uses
the `optimization` environment. The generated Harbor job mounts the file
read-only at `/opt/gurobi/gurobi.lic`. Neither the credential bytes nor its WLS
values enter a task, image, or recorded JSON file. `harbor-job.json` records the
host path and mount settings.

Omit the option and environment variable to use the optimization image with
its license-free solvers.

## Validate the license

Before a course run, build the shipped image and solve a one-variable model in
it. This is live validation. It needs Docker, network access to Gurobi WLS, and
an active license.

```bash
docker build \
  --file src/agentic_assessment_toolkit/templates/environments/optimization.Dockerfile \
  --tag aat-optimization-license-check .

docker run --rm -i \
  --mount "type=bind,source=$AAT_GUROBI_LICENSE_FILE,target=/opt/gurobi/gurobi.lic,readonly" \
  aat-optimization-license-check python - <<'PY'
import gurobipy as gp

model = gp.Model("license-check")
x = model.addVar(lb=0, name="x")
model.addConstr(x <= 1)
model.setObjective(x, gp.GRB.MAXIMIZE)
model.optimize()
assert model.Status == gp.GRB.OPTIMAL
assert abs(model.ObjVal - 1) < 1e-9
print("Gurobi WLS license check passed")
PY
```

Start an Academic WLS course run with one concurrent trial unless the license
portal shows capacity for more. The read-only mount prevents the container from
changing the file, but code inside the container can still read it. Use a
dedicated, renewable credential.
