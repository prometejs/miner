# miner — Usage

Everything needed to install, configure, run, and operate the server. For how
it works internally, see [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Prerequisites

- **Bitcoin Core** full node, synced, with:

  ```ini
  # bitcoin.conf
  server=1
  rpcuser=<user>            # or rpcauth / cookie auth
  rpcpassword=<password>
  zmqpubrawblock=tcp://0.0.0.0:3000   # strongly recommended (instant new-block work)
  ```

  Testnet4 is the recommended proving ground: run Core with `-chain=testnet4`
  (RPC port 48332). Unpruned is recommended for mining nodes.

- **Linux x86-64** for the prebuilt binary, or **Python 3.13** to run from
  source (the package pins `==3.13.*`, so pip refuses other versions).

- A **payout address** for the network you mine on — it is the stratum
  username, and a found block pays it directly in the coinbase. Supported
  types: p2pkh, p2sh, p2wpkh (bech32), p2wsh, p2tr (bech32m).

## 2. Install

**Prebuilt Linux binary (recommended)** — a standalone executable published to
GHCR as an OCI artifact; pull it with [ORAS](https://oras.land):

```bash
oras pull ghcr.io/<owner>/miner:latest && chmod +x miner
```

**pip from git** (the supported package path — there is no PyPI listing):

```bash
pip install "git+https://github.com/<owner>/miner.git@v0.1.0"
```

**Development:**

```bash
git clone https://github.com/<owner>/miner && cd miner
python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## 3. Configuration

All configuration is via environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `BITCOIN_RPC_URL` | `http://127.0.0.1` | Core RPC host (scheme optional) |
| `BITCOIN_RPC_PORT` | `8332` | Core RPC port (`48332` for testnet4) |
| `BITCOIN_RPC_USER` / `BITCOIN_RPC_PASSWORD` | — | RPC credentials |
| `BITCOIN_RPC_COOKIEFILE` | — | Path to Core's `.cookie`; overrides user/password |
| `BITCOIN_RPC_TIMEOUT` | `10000` | RPC timeout, milliseconds |
| `BITCOIN_ZMQ_HOST` | — | e.g. `tcp://127.0.0.1:3000`; unset → 500 ms polling |
| `NETWORK` | `mainnet` | `mainnet` \| `testnet` \| `regtest` — gates address validation |
| `STRATUM_PORT` | `3333` | Miner-facing TCP port |
| `API_PORT` | `3334` | JSON status API (keep internal) |
| `POOL_IDENTIFIER` | `Prometejs` | Tag embedded in the coinbase script (auto-dropped if it would oversize the script/block) |
| `DEV_FEE_ADDRESS` | — | Optional 1.5% coinbase split for miners ≥ 50 TH/s; leave unset for 100% to the miner |
| `QUANTA_POLICY` | `random` | `random` \| `sequential` (deterministic work-sequence assignment) |

## 4. Run

```bash
BITCOIN_RPC_URL=http://127.0.0.1 BITCOIN_RPC_PORT=48332 \
BITCOIN_RPC_USER=user BITCOIN_RPC_PASSWORD=pass \
BITCOIN_ZMQ_HOST=tcp://127.0.0.1:3000 \
NETWORK=testnet QUANTA_POLICY=sequential \
miner
```

Startup logs to watch for: `Bitcoin RPC connected` → `Using ZMQ at ...` →
`stratum listening on :3333` → `api listening on :3334` → first
`template <id>: height=... txs=...` line. `Could not reach RPC host` means
credentials/host are wrong — the server keeps running and retrying.

## 5. Point miners at it

| Field | Value |
|---|---|
| Pool URL | `stratum+tcp://<host>:3333` |
| Worker / user | `<payout-address>` or `<payout-address>.<workername>` |
| Password | `x` (ignored), or `d=65536` to request a starting difficulty |

- **Antminer** (stock UI): set all three pool slots; slot 1 = this server.
  Configure a second self-hosted instance or a public solo pool as slot 2 for
  failover.
- **cpuminer** (end-to-end test without an ASIC):

  ```bash
  cpuminer -a sha256d -o stratum+tcp://127.0.0.1:3333 \
    -u tb1q...youraddress.test -p x
  ```

- Wrong-network addresses are rejected at `mining.authorize` (a mainnet
  address cannot authorize against a testnet server).
- Version rolling is negotiated automatically (`mining.configure`, mask
  `1fffe000`) — required for full hashrate on S17/S19/S21-class hardware.

## 6. Status API

All endpoints return JSON. Keep this port off the public internet.

**`GET /info`** — pool-wide state:

```json
{
  "pool": "Prometejs", "network": "testnet", "uptime": 5231.7,
  "connectedSessions": 3,
  "currentTemplate": {"id": "2a", "height": 2442185, "txCount": 5,
                      "networkDifficulty": 78911.5, "timestamp": 1689514988},
  "blocksFound": 0
}
```

**`GET /quanta`** — the quanta ledger: one record per miner connection
(quantum = its extranonce1) plus totals:

```json
{
  "quanta": [{
    "extranonce1": "00000001", "address": "tb1q...", "worker": "s19-rack1-03",
    "userAgent": "bitaxe", "sessionStart": 1786464000.1, "sessionEnd": null,
    "acceptedShares": 412, "rejectedShares": 3,
    "sumDifficulty": 26214400.0, "bestDifficulty": 891234.2,
    "lastShareAt": 1786464683.6, "impliedHashes": 1.1258e+17,
    "effectiveHashrate": 98700000000000.0, "blocksFound": 0
  }],
  "totals": {"activeQuanta": 1, "acceptedShares": 412,
             "bestDifficulty": 891234.2,
             "effectiveHashrate": 98700000000000.0, "blocksFound": 0}
}
```

`effectiveHashrate` vs the machine's nameplate is the sick-hashboard signal:
a 104 TH/s S19j Pro consistently showing ~70 TH/s effective has a dead board.

**`GET /blocks`** — blocks found this run (height, address, worker, quantum,
RPC result, timestamp).

## 7. Operations

**Recommended rollout**: testnet4 first — point one miner (or cpuminer) at it,
confirm accepted shares in `/quanta`, and ideally wait for a real testnet
block find (block submission is the one path only a live network exercises).
Only then switch `NETWORK=mainnet`, `BITCOIN_RPC_PORT=8332`, and mainnet
payout addresses.

**When a block is found**: the log prints `!!! BLOCK FOUND !!!` with height
and difficulty, the block is pushed via `submitblock` (`SUCCESS!` in the log ⇒
Core accepted it), the reward sits in your address's coinbase output, spendable
after 100 confirmations, and the event appears in `/blocks` and the quantum's
`blocksFound`.

**Vardiff behavior**: new sessions start at difficulty 100 000 (0.1 for
cpuminer). Expect a few `mining.set_difficulty` adjustments in the first
minutes as the server converges on ~1 share per 10 s per miner. A miner
sending `mining.suggest_difficulty` gets that value immediately.

**Restarts are cheap**: no persistent state yet — miners reconnect, get fresh
quanta, and resume within seconds. (Consequence: the quanta ledger and blocks
list reset on restart; persistence is on the roadmap.)

**Troubleshooting:**

| Symptom | Likely cause |
|---|---|
| Miner connects, no work | No template yet — check RPC credentials and `template ...` log lines; node still syncing (`getblockchaininfo` → `initialblockdownload: false`) |
| `Job not found` (error 21) rejects | Shares raced a template refresh — a handful around new blocks is normal; sustained 21s mean the miner ignores `clean_jobs` |
| `Difficulty too low` (23) storms | Miner's real hashrate far below session difficulty — let vardiff converge, or set `d=N` password |
| `Duplicate share` (22) | Firmware resubmitting; a trickle is harmless |
| Authorize fails | Address/network mismatch (e.g. `bc1...` on `NETWORK=testnet`) |
| Work always ~60 s stale on new blocks | ZMQ not connected — verify `BITCOIN_ZMQ_HOST` and Core's `zmqpubrawblock`; watch for `ZMQ new block` log lines |
| High reject after difficulty change | Transient — retargets ship a `clean_jobs` job; sustained rejects suggest network latency to the server |

## 8. Development

```bash
.venv/bin/python -m pytest          # 50 tests, < 1 s
```

The suite includes golden-vector regression tests
(`tests/fixtures/golden_vectors.json`) that freeze the stratum wire format
byte-for-byte against a recorded real-miner session. A deliberate wire-format
change means regenerating that fixture from a verified-good build in its own
commit, with the reasoning in the PR.

### Versioning & release lifecycle

Versioning is **dynamic** (setuptools-scm): git tags are semver (`vX.Y.Z`) and
there is no version field to bump anywhere. A build at a tag is `X.Y.Z`; a
build between tags is `X.Y.(Z+1).devN+g<sha>`.

**One version everywhere.** The version the binary reports is also its registry
tag (with `+` written as `-`, since tags cannot contain `+`), so
`ghcr.io/<owner>/miner:1.2.4.dev3-g2e0ab7abd` holds a binary that reports
`1.2.4.dev3+g2e0ab7abd`, and `:1.2.3` holds one that reports `1.2.3`.

`main` is the source of truth. Two workflows (`.github/workflows/`):

**`main.yml`** — the pipeline (tests gate everything; make `test` a required
check in branch protection). Every run compiles a standalone Linux x86-64
binary with Nuitka and pushes it to GHCR with ORAS under its version plus one
moving alias; main and release builds are also attested:

- *PR*: `<version>` + `pr-<N>`. Fork PRs have a read-only token, so their run
  fails at the push.
- *Merge to main*: `<version>` + `edge`.
- *Release published*: built at the tag, `X.Y.Z` + `latest`. The run fails if
  the built version does not equal the release tag.
- *Pre-release published*: version tag only; `latest` does not move. The tag
  is compared in PEP 440 form, so `v1.3.0-rc1` is published as `1.3.0rc1`.

**`ghcr.yml`** — registry cleanup (weekly cron / manual dispatch with dry-run):
sweeps dev builds (`*.dev*`) and `pr-*` aliases older than 7 days, plus
untagged manifests. Release tags, `latest`, and `edge` are never touched.
