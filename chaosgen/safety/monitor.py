import threading
import time
import logging
from typing import Callable, Optional

class DeadMansSwitch:
    """
    Safety mechanism that monitors system health in the background
    and triggers an emergency stop/rollback if health checks fail.
    """

    def __init__(self, check_fn: Callable[[], bool], trigger_fn: Callable[[], None], interval: int = 5):
        """
        Initialize the Dead Man's Switch.

        Args:
            check_fn: Function that returns True if system is healthy, False otherwise.
            trigger_fn: Function to call when health check fails (e.g., rollback).
            interval: Polling interval in seconds.
        """
        self.check_fn = check_fn
        self.trigger_fn = trigger_fn
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.logger = logging.getLogger("DeadMansSwitch")

    def start(self):
        """Start the background monitoring thread."""
        if self.thread and self.thread.is_alive():
            return
        
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        self.logger.info("Dead Man's Switch activated.")

    def stop(self):
        """Stop the background monitoring thread."""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        self.logger.info("Dead Man's Switch deactivated.")

    def _monitor_loop(self):
        """Internal loop for monitoring system health."""
        while not self.stop_event.is_set():
            try:
                is_healthy = self.check_fn()
                if not is_healthy:
                    self.logger.critical("Health check failed! Triggering emergency rollback.")
                    self.trigger_fn()
                    self.stop_event.set() # Stop monitoring after triggering
                    return
            except Exception as e:
                self.logger.error(f"Error in Dead Man's Switch check: {e}")
                # Optional: Decide if exception counts as failure. For safety, usually yes.
                self.trigger_fn()
                self.stop_event.set()
                return

            time.sleep(self.interval)




