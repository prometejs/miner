"""Environment configuration (12-factor style: everything via env vars)."""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    bitcoin_rpc_url: str = "http://127.0.0.1"
    bitcoin_rpc_port: int = 8332
    bitcoin_rpc_user: str = ""
    bitcoin_rpc_password: str = ""
    bitcoin_rpc_cookiefile: str = ""
    bitcoin_rpc_timeout: float = 10.0
    bitcoin_zmq_host: str = ""  # e.g. tcp://127.0.0.1:3000 (rawblock)
    network: str = "mainnet"  # mainnet | testnet | regtest
    stratum_port: int = 3333
    api_port: int = 3334
    pool_identifier: str = "Prometejs"
    dev_fee_address: str = ""
    quanta_policy: str = "random"  # random | sequential
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            bitcoin_rpc_url=env.get("BITCOIN_RPC_URL", "http://127.0.0.1"),
            bitcoin_rpc_port=int(env.get("BITCOIN_RPC_PORT", "8332")),
            bitcoin_rpc_user=env.get("BITCOIN_RPC_USER", ""),
            bitcoin_rpc_password=env.get("BITCOIN_RPC_PASSWORD", ""),
            bitcoin_rpc_cookiefile=env.get("BITCOIN_RPC_COOKIEFILE", ""),
            bitcoin_rpc_timeout=int(env.get("BITCOIN_RPC_TIMEOUT", "10000")) / 1000,
            bitcoin_zmq_host=env.get("BITCOIN_ZMQ_HOST", ""),
            network=env.get("NETWORK", "mainnet"),
            stratum_port=int(env.get("STRATUM_PORT", "3333")),
            api_port=int(env.get("API_PORT", "3334")),
            pool_identifier=env.get("POOL_IDENTIFIER", "Prometejs"),
            dev_fee_address=env.get("DEV_FEE_ADDRESS", ""),
            quanta_policy=env.get("QUANTA_POLICY", "random"),
        )

    @property
    def rpc_base_url(self) -> str:
        url = self.bitcoin_rpc_url
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        # replace/append port
        scheme, rest = url.split("://", 1)
        host = rest.split("/", 1)[0].split(":", 1)[0]
        return f"{scheme}://{host}:{self.bitcoin_rpc_port}"
