"""SECC entry point for the Monday communication-only real-EV test."""

import asyncio
import logging

from iso15118.secc import SECCHandler
from iso15118.secc.controller.interface import ServiceStatus
from iso15118.secc.controller.monday_hardware import MondayHardwareEVSEController
from iso15118.secc.secc_settings import Config
from iso15118.shared.exificient_exi_codec import ExificientEXICodec


logger = logging.getLogger(__name__)


async def main():
    config = Config()
    config.load_envs()
    config.print_settings()

    controller = MondayHardwareEVSEController()
    await controller.set_status(ServiceStatus.STARTING)
    await SECCHandler(
        exi_codec=ExificientEXICodec(),
        evse_controller=controller,
        config=config,
    ).start(config.iface)


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.debug("Monday SECC terminated manually")


if __name__ == "__main__":
    run()
