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
