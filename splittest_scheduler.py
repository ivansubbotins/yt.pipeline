"""Split-test scheduler — runs as PM2 process, checks for rotations every 5 minutes."""

import logging
import time

from splittest import get_all_running, rotate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SplitTest] %(message)s",
)
logger = logging.getLogger(__name__)

CHECK_INTERVAL = 300  # 5 minutes

if __name__ == "__main__":
    logger.info("Split-test scheduler started")
    while True:
        try:
            running = get_all_running()
            if running:
                logger.info(f"Checking {len(running)} running split-tests")
                for pid in running:
                    try:
                        result = rotate(pid)
                        if result:
                            logger.info(f"Rotated: {pid}")
                    except Exception as e:
                        logger.error(f"Error rotating {pid}: {e}")
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        time.sleep(CHECK_INTERVAL)
