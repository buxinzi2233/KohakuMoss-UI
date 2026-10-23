"""Open the local URL in the system browser."""

import threading
import time
import webbrowser


def open_url(url: str, delay: float = 0.4) -> None:
    """Open once, off the server thread; failures are reported by the caller."""

    def run() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
