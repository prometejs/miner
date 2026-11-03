import asyncio
import logging

from .config import Settings
from .telemetry import configure_telemetry
from .stratum.server import StratumServer


def cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    shutdown_telemetry = configure_telemetry()
    try:
        server = StratumServer(settings)
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    cli()
