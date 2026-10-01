"""miner: self-hosted Bitcoin solo-mining stratum server with a
native quanta layer (per-miner work-space assignment and accounting)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("miner")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"
