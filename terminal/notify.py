"""macOS notification for critical alerts."""
import logging
import subprocess
import sys

log = logging.getLogger("terminal.notify")
# The script is fixed; the alert text arrives as arguments, so it can never run as AppleScript.
_SCRIPT = ("osascript", "-e", "on run argv", "-e",
           'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"', "-e", "end run")


def mac_notify(title: str, body: str, run=subprocess.Popen, platform: str = sys.platform) -> None:
    if platform != "darwin":
        return
    try:
        run([*_SCRIPT, title[:200], body[:400]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.warning("could not show a notification: %s", exc)
