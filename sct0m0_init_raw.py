"""
sct0m0_init_raw.py
----------------------------------------------------------------------
Initializes the NIDEC SANKYO SCT0M0 dispenser over raw RS-232, using
the real protocol from the vendor Interface Specification
(ASL-NP-33820-01) - NOT via SCT0M0_0130DLL.dll.

This talks straight to the serial port via pyserial. No 32-bit Python
requirement, no DLL needed - just this script + sct0m0_protocol.py
in the same folder.

Sequence performed:
    1. CCB INITIALIZE  (spec 6.1)  - initializes the comms board
    2. SCT INITIALIZE  (spec 10.1) - initializes the card mechanism
    3. SCT STATUS REQUEST (spec 10.2) - confirms current state

LOGGING:
    Every run appends to sct0m0_init.log in this same folder - one
    timestamped line per step, plus a final SUMMARY line recording the
    overall outcome (SUCCESS / SUCCESS_WITH_WARNING / NEGATIVE_REPLY /
    PROTOCOL_ERROR / CONNECTION_FAILED). The log file is never
    overwritten or rotated - it just grows, so you have a full history
    of every run. Everything printed to the console is also written
    to the log file.

Install dependency first:
    pip install pyserial
----------------------------------------------------------------------
"""

import sys
import logging
from pathlib import Path
from sct0m0_protocol import (
    SCT0M0Link, CCB, SCT, NegativeReply, ProtocolError, describe_error,
)

COM_PORT = "COM7"
BAUD_RATE = 115200

# NOTE on baud rate:
# Per the Interface Specification (section 1.1), the CCB autodetects the
# baud rate ONCE, right after power-on, based on whatever the HOST sends
# first. It then locks to that speed for the rest of the session and
# cannot be switched without a power cycle. Supported speeds are 9600,
# 19200, 38400, and 115200.
#
# This means the "correct" baud rate isn't necessarily fixed in firmware -
# it's whatever got negotiated at the last power-up. If you power-cycle
# the unit and this script then fails to connect, try the other supported
# speeds (9600 / 19200 / 38400) before assuming something else is wrong.

LOG_FILE = Path(__file__).resolve().parent / "sct0m0_init.log"


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("sct0m0_init")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()  # avoid duplicate handlers if main() is re-run in one process

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def main():
    log = setup_logging()
    log.info("=" * 70)
    log.info(f"Run started - target {COM_PORT} @ {BAUD_RATE} baud (8E1)")

    try:
        link = SCT0M0Link(COM_PORT, BAUD_RATE, timeout=5.0)
    except Exception as e:
        log.error(f"Could not open {COM_PORT}: {e}")
        log.info(f"SUMMARY: outcome=CONNECTION_FAILED port={COM_PORT} "
                 f"baud={BAUD_RATE} detail={e}")
        sys.exit(1)

    outcome = "UNKNOWN"
    detail = ""

    with link:
        ccb = CCB(link)
        sct = SCT(link)

        # ---- Step 1: Initialize the Contactless Communication Board ----
        log.info("[CCB INITIALIZE] sending...")
        try:
            resp = ccb.initialize()
            log.info(f"[CCB INITIALIZE] OK - status={resp.status_or_error!r}")
        except NegativeReply as e:
            msg = describe_error("ccb", e.error_code)
            log.error(f"[CCB INITIALIZE] NEGATIVE - error {e.error_code}: {msg}")
            log.error("  (Usually means the User program code area is in a "
                       "bad state, or autobaud didn't lock - power-cycle and retry.)")
            outcome, detail = "NEGATIVE_REPLY", f"CCB INITIALIZE error={e.error_code} ({msg})"
            log.info(f"SUMMARY: outcome={outcome} step='CCB INITIALIZE' detail='{detail}'")
            return
        except ProtocolError as e:
            log.error(f"[CCB INITIALIZE] PROTOCOL ERROR: {e}")
            outcome, detail = "PROTOCOL_ERROR", f"CCB INITIALIZE: {e}"
            log.info(f"SUMMARY: outcome={outcome} step='CCB INITIALIZE' detail='{detail}'")
            return

        # ---- Step 2: Initialize the card transport mechanism (SCT) ----
        log.info("[SCT INITIALIZE] sending (pm=0, sh=0, cp=0)...")
        try:
            resp = sct.initialize(pm="0", sh="0", cp="0")
            st1 = resp.status_or_error[0:1]
            st0 = resp.status_or_error[1:2]
            st2 = resp.status_or_error[2:3]
            log.info(f"[SCT INITIALIZE] OK - st1={st1!r} st0={st0!r} "
                     f"st2(hopper)={st2!r}")
        except NegativeReply as e:
            msg = describe_error("sct", e.error_code)
            log.error(f"[SCT INITIALIZE] NEGATIVE - error {e.error_code}: {msg}")
            if e.error_code == "SA":
                log.error("  SCT is in its Supervisor program area - needs a "
                           "firmware download (spec section 8.3) before use.")
            outcome, detail = "NEGATIVE_REPLY", f"SCT INITIALIZE error={e.error_code} ({msg})"
            log.info(f"SUMMARY: outcome={outcome} step='SCT INITIALIZE' detail='{detail}'")
            return
        except ProtocolError as e:
            log.error(f"[SCT INITIALIZE] PROTOCOL ERROR: {e}")
            outcome, detail = "PROTOCOL_ERROR", f"SCT INITIALIZE: {e}"
            log.info(f"SUMMARY: outcome={outcome} step='SCT INITIALIZE' detail='{detail}'")
            return

        # ---- Step 3: Confirm status ----
        log.info("[SCT STATUS REQUEST] sending (pm=0)...")
        try:
            resp = sct.status_request(pm="0")
            log.info(f"[SCT STATUS REQUEST] OK - status bytes: {resp.status_or_error!r}")
            outcome, detail = "SUCCESS", f"status={resp.status_or_error!r}"
        except NegativeReply as e:
            msg = describe_error("sct", e.error_code)
            log.error(f"[SCT STATUS REQUEST] NEGATIVE - error {e.error_code}: {msg}")
            # Init itself succeeded even if this confirmation step didn't
            outcome, detail = "SUCCESS_WITH_WARNING", (
                f"init OK but STATUS REQUEST returned error={e.error_code} ({msg})"
            )
        except ProtocolError as e:
            log.error(f"[SCT STATUS REQUEST] PROTOCOL ERROR: {e}")
            outcome, detail = "SUCCESS_WITH_WARNING", (
                f"init OK but STATUS REQUEST hit a protocol error: {e}"
            )

        log.info("Initialization sequence complete. Card transport is "
                  "currently DISABLED (per spec 10.7). Call sct.enable() "
                  "if you want it to start accepting cards.")
        log.info(f"SUMMARY: outcome={outcome} detail='{detail}'")


if __name__ == "__main__":
    main()
