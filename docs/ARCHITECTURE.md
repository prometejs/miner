# prometejs-miner — Architecture

`prometejs-miner` is a self-hosted Bitcoin **solo-mining Stratum V1 server**: it
turns your own Bitcoin Core node's block templates into mining work, distributes
that work to ASIC miners with per-miner search-space assignment ("**quanta**"),
validates the shares that come back, and submits any share meeting network
difficulty as a real block — with the coinbase paying the miner's own address
(non-custodial: the pool never touches funds).

Its stratum wire format is **frozen byte-for-byte** by golden-vector
regression tests built from a recorded real-miner session (§7).
Inspired by [public-pool](https://github.com/benjamin-wilson/public-pool).

## 1. System context

```
┌──────────────┐  getblocktemplate / submitblock (JSON-RPC)   ┌─────────────────┐
│ Bitcoin Core │◄─────────────────────────────────────────────┤ prometejs-miner │
│  full node   ├─────────────────────────────────────────────►│                 │
│              │  rawblock notifications (ZMQ pub/sub)        │  stratum :3333  │
└──────────────┘                                              │  api     :3334  │
                                                              └───────┬─────────┘
                                                    Stratum V1 (TCP, line-JSON)
                                            ┌─────────────────────────┼─────────┐
                                            │                         │         │
                                       ┌────┴─────┐             ┌─────┴────┐   ...
                                       │ Antminer │             │  Bitaxe/ │
                                       │ S9..S21  │             │ cpuminer │
                                       └──────────┘             └──────────┘
```

- **Upstream dependency**: one Bitcoin Core node (`server=1`, ZMQ recommended).
- **Downstream clients**: anything speaking Stratum V1 — Antminers on stock or
  aftermarket firmware, Bitaxes, cpuminer for testing. Version-rolling
  (BIP 310) is negotiated, which modern ASICs require for full hashrate.
- **No database server, no message broker**: state is in-process; persistence
  is deliberately minimal at this stage (see §9 roadmap).

## 2. Module map

```
src/                            import name: prometejs_miner
├── main.py                     entrypoint: logging + Settings.from_env + serve
├── config.py                   Settings dataclass; all configuration via env vars
├── bitcoin/                    consensus-level primitives (stdlib only, no deps)
│   ├── crypto.py               sha256d, merkle root, stratum merkle branch + fold
│   ├── encoding.py             varint, BIP34 script numbers, base58check, bech32/bech32m,
│   │                           address → scriptPubKey (p2pkh/p2sh/p2wpkh/p2wsh/p2tr),
│   │                           stratum word-swap
│   ├── coinbase.py             coinbase tx construction + (non)witness serialization,
│   │                           extranonce padding, segwit commitment output, weight
│   └── difficulty.py           nBits → network difficulty, hash → share difficulty
├── jobs/
│   ├── template.py             GBT result → JobTemplate (merkle branches, witness
│   │                           commitment, work-signature dedupe)
│   ├── mining_job.py           per-client job: coinb1/coinb2 split, notify payload,
│   │                           header build, full-block build for submitblock
│   ├── manager.py              registries: live templates + jobs, ids, 5-min expiry
│   └── watcher.py              template refresh loop (new-block event OR 60s interval)
├── rpc/
│   └── bitcoin_rpc.py          aiohttp JSON-RPC client + ZMQ rawblock watcher
│                               (falls back to 500ms getmininginfo polling)
├── stratum/
│   ├── server.py               TCP accept loop + aiohttp status API, shared state
│   ├── session.py              per-connection protocol state machine
│   ├── messages.py             parse/validate the 5 request types, exact-format responses
│   └── vardiff.py              share-rate statistics + difficulty retargeting
└── quanta/
    ├── allocator.py            extranonce1 assignment policies (random | sequential)
    └── ledger.py               per-quantum accounting (shares, best diff, hashrate)
```

Dependency direction is strictly downward: `stratum` → `jobs` → `bitcoin`;
`quanta` and `rpc` are leaves consumed by `stratum`. `bitcoin/` has zero
third-party imports — every consensus-critical byte is produced by auditable
stdlib code.

## 3. Concurrency model

Single process, single thread, one asyncio event loop.

- One `StratumSession` coroutine per miner TCP connection (created by
  `asyncio.start_server`), plus a per-session 60-second vardiff timer task.
- One `TemplateWatcher` task that waits on (new-block event | 60s timeout).
- One block watcher task (ZMQ subscribe loop, or 500ms poll loop).
- The aiohttp status API runs on the same loop.

Why this is enough: vardiff targets ~1 share per 10 seconds per miner, so 100
miners ≈ 10 messages/second, and validating a share is a handful of SHA-256d
invocations over ≤ a few hundred bytes. The only latency-critical path is
new-block → `clean_jobs` notify fan-out, which is one template build plus N
socket writes — microseconds-to-milliseconds. CPython is not a bottleneck
below thousands of connections (that world belongs to ckpool).

## 4. Work pipeline (node → miner)

1. **Block watcher** (`rpc/bitcoin_rpc.py`): ZMQ `rawblock` message (or poll)
   → `getmininginfo` → if height advanced, set the new-block event.
2. **Template refresh** (`jobs/watcher.py`): on new-block event or 60s tick →
   `getblocktemplate {"rules":["segwit"]}` →
   - `clear_jobs = height changed` (miners must abandon work),
   - work-signature dedupe (prevhash|version|bits|time|height|value|txids) so
     unchanged templates are not re-broadcast,
   - `build_template()` (below),
   - registries updated (`clear_jobs` wipes them; otherwise 5-min expiry),
   - fan-out to every subscribed session.
3. **Template build** (`jobs/template.py`), per template:
   - merkle **branch** for the coinbase position from template txids,
   - **witness commitment** = sha256d(witness-merkle-root ‖ 32×00) — verified
     in tests against Core's own `default_witness_commitment`,
   - `timestamp = max(mintime, now)`, nBits → float network difficulty.
4. **Job build** (`jobs/mining_job.py`), per session per template (each miner
   gets its *own* job because the coinbase pays *its* address):
   - coinbase tx: BIP34 height + pool identifier + 12 zero bytes reserved for
     `extranonce1(4) ‖ extranonce2(8)` in the input script; payout output(s);
     OP_RETURN segwit commitment; witness reserved value,
   - identifier dropped automatically if script > 100 bytes or block weight
     would exceed 4M WU,
   - non-witness serialization split into `coinb1 ‖ [extranonces] ‖ coinb2`,
   - `mining.notify` line in compact JSON — the layout locked by the golden vectors.

## 5. Stratum session lifecycle

```
miner                                   prometejs-miner
  │ mining.configure ────────────────────► version-rolling mask 1fffe000 (BIP 310)
  │ mining.subscribe ────────────────────► extranonce1 (quantum) + extranonce2_size=8
  │ mining.suggest_difficulty (optional) ► honored once, echoed as set_difficulty
  │ mining.authorize (address[.worker]) ─► address validated for network; "d=N"
  │                                        password sets starting difficulty
  │                    ◄──────────────────  set_difficulty (if none suggested)
  │                    ◄──────────────────  mining.notify (current job)
  │ mining.submit ───────────────────────► validate → accept / reject
  │                    ◄──────────────────  notify on every template refresh;
  │                                          set_difficulty on vardiff retarget
```

**Submit validation order**: job lookup (stale → error 21)
→ template lookup (21) → duplicate check on
`jobId:extranonce2:ntime:nonce:versionMask` (22) → header reconstruction →
share difficulty = `TRUE_DIFF_ONE / hash-as-le-double` → below session
difficulty → 23; otherwise **accepted**, credited to vardiff statistics and
the quanta ledger, and — if difficulty ≥ network difficulty — the full block
is serialized (header ‖ txcount ‖ witness-serialized coinbase ‖ raw template
txs) and pushed via `submitblock`.

**Vardiff**: starts at 100 000 (0.1 for cpuminer user-agents); every 60s the
session's recent share rate is converted to a difficulty-per-second estimate
and retargeted to the nearest power of two when the current difficulty is off
by more than 2× either way; a silent miner is stepped down (÷6 → pow2). A
retarget re-sends the current job with `clean_jobs=true` so it takes effect
immediately.

## 6. The quanta layer

A **quantum** is the disjoint slice of the search space assigned to one miner
connection, identified by its **extranonce1**. Because extranonce1 is embedded
in the coinbase transaction, two miners with different extranonce1 can never
hash the same candidate header — disjointness is structural, not cooperative.

- **Allocator** (`quanta/allocator.py`): `QUANTA_POLICY=random` assigns 4
  random bytes per connection (collision-checked); `sequential` assigns
  slices in a deterministic incrementing work sequence — the "explore this
  part of the space in this order" control, made explicit and auditable.
- **Ledger** (`quanta/ledger.py`): per quantum — accepted/rejected share
  counts, Σ difficulty, best share, `implied_hashes = Σdiff × 2³²`, effective
  hashrate (compare against nameplate to spot sick hashboards), blocks found,
  session lifetime. Exposed at `GET /quanta`.

**Honest design note**: search order cannot change expected time-to-block —
SHA-256d is uniform and memoryless, so every enumeration order has identical
odds (see the parent repo's `docs/research/03-strategies.md` §5 for the
proof). The quanta layer exists for duplicate-work guarantees, attribution,
and telemetry. It is deliberately *not* marketed as a luck optimizer.

## 7. Correctness strategy

The central risk in mining-work construction is silent byte drift (endianness,
merkle ordering, coinbase serialization) — a wrong byte doesn't crash, it
quietly mines invalid work. Defense in depth:

1. **Reference vectors** (`tests/test_encoding.py`): BIP 173/350 addresses,
   base58, BIP34 heights, varints.
2. **Node cross-check** (`tests/test_mining_job.py`): our witness commitment
   must equal the `default_witness_commitment` Bitcoin Core itself computed in
   the recorded fixture.
3. **Golden vectors** (`tests/test_golden_vectors.py`): every derived byte for
   a recorded real-miner session — template fields, merkle branches,
   coinb1/coinb2, the full notify line, share headers, submitblock hex — is
   frozen in `tests/fixtures/golden_vectors.json`; pytest asserts exact
   equality. Any wire-format change must be deliberate: regenerate the fixture
   in its own commit and justify it.
4. **Protocol-level replay** (`tests/test_session.py`): the recorded session
   driven through a live `StratumSession`; response strings asserted exactly.
5. **Socket smoke** (`tests/test_server_smoke.py`): full server over real TCP
   + HTTP, handshake through accepted share through API state.

## 8. Configuration & deployment surfaces

All configuration is via environment variables — full table in
[USAGE.md](USAGE.md). Deployables:
Docker image (GHCR, built by `release.yml` on `v*` tags), pip-from-git
package, or a bare venv run of the `prometejs-miner` console command. The status API is intended
to stay on localhost / an internal network; the stratum port faces only the
miner VLAN. Nothing here should ever be internet-exposed.

## 9. Deliberate omissions & roadmap

Deliberately out of scope for now: database persistence, chat-bot
notifications (Telegram/Discord), payment-processor integrations, and
multi-process clustering (one asyncio process covers the target scale).

Planned: Prometheus `/metrics`, persistent quanta ledger, Grafana dashboards,
dynamic versioning via git tags, and — post-MVP — a Stratum V2 listener.
