# Benchmarks

`scripts/run_benchmark.py` runs Agent commands from one strict JSON manifest. It uses only
the Python standard library, never invokes a shell string, copies a separate workspace for
every Agent and task, and refuses to overwrite an existing report.

The included remote smoke manifest uses the configured SiliconFlow model:

```bash
export OA3_SILICONFLOW_API_KEY="..."
python3.11 scripts/run_benchmark.py \
  --manifest examples/benchmark.json \
  --output /tmp/super-agent-benchmark
```

The key is inherited by the child process through its existing environment. The manifest
contains only the environment variable name and never stores the secret value.

Every Agent entry declares its exact version, argument array, non-secret environment, and
optional JSON result field. Place `{prompt}`, `{workspace}`, `{project_root}`, or `{python}`
inside individual arguments instead of constructing a shell command. Tasks may start from an
empty directory or a copied workspace.

The report records the manifest SHA-256, exact task output and digest, exit status, timeout,
and elapsed time. A completed process is not automatically a correct answer; benchmark-specific
scoring is added separately so generation and evaluation remain auditable.
