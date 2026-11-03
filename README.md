# miner

Self-hosted Bitcoin **solo-mining** Stratum V1 server with a native **quanta**
layer (per-miner work-space assignment and accounting). Non-custodial by
construction: a found block pays the miner's own address directly in the
coinbase. The stratum wire format is frozen by byte-for-byte golden-vector
regression tests built from a recorded real-miner session.

Inspired by [public-pool](https://github.com/benjamin-wilson/public-pool).

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — system context, module map,
  concurrency model, work pipeline, stratum lifecycle, quanta design, and the
  correctness/test strategy
- **[docs/USAGE.md](docs/USAGE.md)** — install, configuration reference, running,
  miner setup, status API, operations & troubleshooting

## Quick start

```bash
oras pull ghcr.io/<owner>/miner:latest && chmod +x miner
# or: pip install "git+https://github.com/<owner>/miner.git@v0.1.0"
```

Point it at your Bitcoin Core node, point your miners at `stratum+tcp://<host>:3333`
with a payout address as username — full walkthrough in [docs/USAGE.md](docs/USAGE.md).

## Development

```bash
python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The release procedure is documented in [docs/USAGE.md](docs/USAGE.md) §8.

## Contributing

Contributions are welcome — read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
first so changes land in the right layer. Ground rules:

1. **The wire format is frozen.** Anything touching work construction
   (`src/bitcoin/`, `src/jobs/`) or protocol responses (`src/stratum/messages.py`)
   must keep the golden-vector tests green. A deliberate wire-format change
   regenerates the fixtures in a separate commit with an explanation in the PR.
2. **Tests accompany code.** New behavior needs a test; consensus-critical code
   needs reference vectors or a golden comparison, not just self-consistency.
   `python -m pytest` must pass — CI runs it plus a Nuitka binary build on every PR.
3. **Keep the dependency direction.** `stratum → jobs → bitcoin`; `src/bitcoin/`
   stays stdlib-only. New third-party dependencies need a strong justification.
4. **Honest claims only.** Features or docs implying luck/EV improvements from
   search-order strategies will be rejected — see the quanta design notes in
   ARCHITECTURE §6.
5. **Flow:** fork or branch from `main`, keep PRs focused (one concern per PR),
   and let CI pass before requesting review. For large changes, open an issue
   describing the approach first.

## OpenTelemetry

The service exports traces and metrics using OTLP/HTTP protobuf when an endpoint
is configured. Export is off by default; existing console logging is unchanged.
For a collector listening on port 4318, start the miner with:

```sh
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=prometejs-miner
uv run miner
```

The collector must enable its OTLP HTTP receiver and traces/metrics pipelines.
The base endpoint automatically receives `/v1/traces` and `/v1/metrics` suffixes.
Alternatively, set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and/or
`OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` to full signal URLs to enable each signal
separately. This integration uses HTTP/protobuf, not gRPC.

Standard SDK/exporter settings support resource attributes, headers, TLS,
timeouts, trace sampling and metric export intervals (see `.env.example`).
Set `OTEL_SDK_DISABLED=true` to disable both signals. The example file is a
reference; the service reads the process environment, not a dotenv file.

Instrumentation includes:

- `stratum.submit` spans for initialized miners' share validation, with nested
  `bitcoin.rpc` client spans when a block is submitted; polling/template RPCs
  also produce client spans.
- `miner.operations` and `miner.operation.duration` (seconds), by operation,
  outcome, and bounded RPC method.
- `miner.connections`, the current number of connected Stratum sessions.
- `miner.shares`, accepted/rejected shares with bounded rejection reasons.
- `miner.block.submissions`, accepted/rejected Bitcoin Core submission results
  (RPC failures are counted as rejected).

Metrics are independent of trace sampling. For a busy miner, consider
`OTEL_TRACES_SAMPLER=parentbased_traceidratio` and `OTEL_TRACES_SAMPLER_ARG=0.1`.
No miner addresses, worker names, job IDs, RPC parameters, block payloads, or
exception messages are attached to telemetry. Logs remain on the console and
are not exported through OTLP. Providers flush/shut down when the CLI exits.
See the [OpenTelemetry Python exporter documentation](https://opentelemetry.io/docs/languages/python/exporters/)
for collector configuration.
