"""
sct0m0_protocol.py
----------------------------------------------------------------------
Raw RS-232 protocol implementation for the NIDEC SANKYO SCT0M0 series
(card dispenser + contactless communication board), based directly on
the vendor Interface Specification (ASL-NP-33820-01, Rev A).

This talks to the device DIRECTLY over the serial port using pyserial -
it does NOT use SCT0M0_0130DLL.dll. This is the "raw serial" approach:
we now have the real protocol, so we don't need the DLL as a black box.

----------------------------------------------------------------------
PROTOCOL SUMMARY (see the spec for full detail)
----------------------------------------------------------------------
Frame format:
    STX(1) LEN(2, big-endian) TEXT(LEN bytes) CRCC(2, big-endian)

    STX   = 0xF2
    LEN   = length of TEXT only, as a 16-bit big-endian value
    CRCC  = CRC-16/CCITT-FALSE-style, poly 0x1021, init 0x0000,
            computed over STX + LEN + TEXT (confirmed against the
            spec's own worked example in ANNEX 1)

Transmission rules:
    - HOST sends a frame, then waits for ACK (0x06) within 300ms.
    - After ACK, HOST waits for the response frame within 60s
      (most commands) and replies with ACK once it's received OK,
      or NAK (0x15) if the CRC didn't check out.
    - There must be >= 5ms gap before HOST sends the next command
      after receiving a response.
    - DLE+EOT (0x10, 0x04) cancels an in-progress command.

Two logical destinations, both reached over the same serial line:
    - CCB commands: TEXT starts with 'c' (lowercase) -- handled by the
      Contactless Communication Board itself.
    - SCT commands: TEXT starts with 'C' (uppercase) -- passed through
      the CCB to the card-transport mechanism (the "SCT" controller).

Command/response text layout:
    Command:           <tag> <cm> <pm> [data...]
    Positive response: <tag> <cm> <pm> <status...> [data...]
    Negative response: <tag> <cm> <pm> <e1> <e0> [data...]

    tag for CCB: 'c' (cmd) / 'p' (positive) / 'n' (negative)
    tag for SCT: 'C' (cmd) / 'P' (positive) / 'N' (negative)

    CCB status field is 2 bytes (st1, st0).
    SCT status field is 3 bytes (st1, st0, st2) -- st2 is hopper status.

----------------------------------------------------------------------
SAFETY NOTE
----------------------------------------------------------------------
This module implements the *initialize / status / revision* commands
fully, since those don't move anything mechanically in a way that's
hard to predict. Card-transport commands (Entry, Eject, Capture,
Retrieve, hopper motor) are also implemented per spec, but you should
read section 10 of the spec yourself before calling them on real
hardware with cards loaded, since they drive physical motors.
----------------------------------------------------------------------
"""

import serial
import time
from dataclasses import dataclass
from typing import Optional


# ----------------------------------------------------------------------
# Control characters (spec section 1.3)
# ----------------------------------------------------------------------
STX = 0xF2
ACK = 0x06
NAK = 0x15
DLE = 0x10
EOT = 0x04

POLY = 0x1021
CRC_INIT = 0x0000


# ----------------------------------------------------------------------
# CRC-16 implementation (spec ANNEX 1) - verified against the manual's
# own worked example: CRC(F2 00 08 43 30 30 33 32 34 30 30) == 0xFACE
# ----------------------------------------------------------------------
def _calc_crc_byte(crc: int, ch: int) -> int:
    ch = (ch << 8) & 0xFFFF
    for _ in range(8):
        if (ch ^ crc) & 0x8000:
            crc = ((crc << 1) ^ POLY) & 0xFFFF
        else:
            crc = (crc << 1) & 0xFFFF
        ch = (ch << 1) & 0xFFFF
    return crc


def get_crc(data: bytes) -> int:
    crc = CRC_INIT
    for b in data:
        crc = _calc_crc_byte(crc, b)
    return crc


# ----------------------------------------------------------------------
# Frame building / parsing
# ----------------------------------------------------------------------
def build_frame(text: bytes) -> bytes:
    """STX + LEN(2B BE) + TEXT + CRCC(2B BE), CRC over STX+LEN+TEXT."""
    if len(text) > 1024:
        raise ValueError("TEXT exceeds max 1024 bytes per spec section 1.4")
    length = len(text)
    header = bytes([STX, (length >> 8) & 0xFF, length & 0xFF])
    body = header + text
    crc = get_crc(body)
    return body + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


class ProtocolError(Exception):
    pass


class NegativeReply(Exception):
    """Raised when the device returns a negative ('n'/'N') response."""
    def __init__(self, cm: str, pm: str, e1: str, e0: str, data: bytes = b""):
        self.cm = cm
        self.pm = pm
        self.error_code = e1 + e0  # e.g. "A0", "10", "02"
        self.data = data
        super().__init__(f"Negative reply: cm={cm} pm={pm} error={self.error_code}")


@dataclass
class Response:
    tag: str            # 'p'/'n' (CCB) or 'P'/'N' (SCT)
    cm: str             # command code echoed back, as the ASCII char(s) sent
    pm: str             # parameter code echoed back
    status_or_error: bytes
    data: bytes
    raw_text: bytes


class SCT0M0Link:
    """
    Low-level serial link implementing the HOST side of the
    NIDEC SANKYO RS-232 protocol (spec section 1.6).
    """

    def __init__(self, port: str, baud: int = 9600, timeout: float = 5.0):
        self.ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,   # spec 1.1: 8bit + 1 parity, even
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
        )
        # Let the line settle / device finish any power-on autobaud detection
        time.sleep(0.2)

    def close(self):
        self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    def _write(self, data: bytes):
        self.ser.write(data)
        self.ser.flush()

    def _read_exact(self, n: int, timeout: float) -> bytes:
        deadline = time.time() + timeout
        buf = b""
        while len(buf) < n and time.time() < deadline:
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf += chunk
        if len(buf) < n:
            raise ProtocolError(
                f"Timed out waiting for {n} bytes (got {len(buf)}: {buf!r})"
            )
        return buf

    # ------------------------------------------------------------------
    def send_command(self, text: bytes, response_timeout: float = 60.0) -> Response:
        """
        Send one command frame and return the parsed response.
        Implements the "Ordinary operation" sequence from spec 1.6.3:
            HOST -> Command frame
            CCB  -> ACK
            CCB  -> Response frame
            HOST -> ACK
        """
        frame = build_frame(text)

        # ---- Send command, wait for ACK (300ms per spec state table) ----
        self._write(frame)
        ack = self._read_exact(1, timeout=0.3)
        if ack[0] != ACK:
            raise ProtocolError(f"Expected ACK after command, got {ack!r}")

        # ---- Wait for response frame ----
        stx = self._read_exact(1, timeout=response_timeout)
        if stx[0] != STX:
            raise ProtocolError(f"Expected STX in response, got {stx!r}")
        len_bytes = self._read_exact(2, timeout=0.25)
        resp_len = (len_bytes[0] << 8) | len_bytes[1]
        resp_text = self._read_exact(resp_len, timeout=0.25)
        crc_bytes = self._read_exact(2, timeout=0.25)
        received_crc = (crc_bytes[0] << 8) | crc_bytes[1]

        check_body = bytes([STX]) + len_bytes + resp_text
        expected_crc = get_crc(check_body)

        if received_crc != expected_crc:
            # Tell the device the frame was bad
            self._write(bytes([NAK]))
            raise ProtocolError(
                f"CRC mismatch: got {received_crc:#06x}, "
                f"expected {expected_crc:#06x}"
            )

        # Frame OK -> ACK it
        self._write(bytes([ACK]))

        # Mandatory >=5ms gap before the next command (spec section 1.5)
        time.sleep(0.01)

        return self._parse_response(resp_text)

    def cancel(self):
        """Send DLE,EOT to cancel an in-progress command (spec 1.6.2)."""
        self._write(bytes([DLE, EOT]))

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_response(text: bytes) -> Response:
        if len(text) < 3:
            raise ProtocolError(f"Response too short: {text!r}")

        tag = chr(text[0])
        if tag in ("p", "n"):
            # CCB response: tag cm pm [st1 st0 | e1 e0] data...
            cm = chr(text[1])
            pm = chr(text[2])
            rest = text[3:]
            status_or_error = rest[:2]
            data = rest[2:]
        elif tag in ("P", "N"):
            # SCT response: tag cm pm [st1 st0 st2 | e1 e0] data...
            cm = chr(text[1])
            pm = chr(text[2])
            rest = text[3:]
            if tag == "P":
                status_or_error = rest[:3]
                data = rest[3:]
            else:
                status_or_error = rest[:2]
                data = rest[2:]
        else:
            raise ProtocolError(f"Unrecognized response tag: {tag!r} in {text!r}")

        resp = Response(
            tag=tag, cm=cm, pm=pm,
            status_or_error=status_or_error, data=data, raw_text=text,
        )

        if tag in ("n", "N"):
            e1 = chr(status_or_error[0])
            e0 = chr(status_or_error[1])
            raise NegativeReply(cm=cm, pm=pm, e1=e1, e0=e0, data=data)

        return resp


# ----------------------------------------------------------------------
# High-level convenience commands, built from the spec's command tables
# ----------------------------------------------------------------------
class CCB:
    """Commands handled directly by the Contactless Communication Board."""

    def __init__(self, link: SCT0M0Link):
        self.link = link

    def initialize(self) -> Response:
        """
        CCB INITIALIZE command (spec section 6.1).
        cm='0'(30H), pm='0'(30H), no extra data documented for the
        User-program-area initialize in 6.1's command line itself.
        """
        text = b"c" + b"0" + b"0"
        return self.link.send_command(text)

    def revision(self) -> Response:
        """CCB REVISION command, User program area (pm='1', section 6.2)."""
        text = b"c" + b"A" + b"1"  # cm=41H='A', pm=31H='1'
        return self.link.send_command(text)


class SCT:
    """Commands passed through the CCB to the card-transport mechanism."""

    def __init__(self, link: SCT0M0Link):
        self.link = link

    def initialize(self, pm: str = "0", sh: str = "0", cp: str = "0") -> Response:
        """
        SCT INITIALIZE command (spec section 10.1).
        Format: "C" 30H pm 30H 30H 30H 30H 30H 30H 30H Sh 30H 30H Cp

        pm: card disposition if a card is inside SCT at init time.
            '0' = move to gate (default, recommended starting point)
            '1' = capture to reject-stacker
            '2' = retain inside SCT
            '3' = do not move the card
            '4'/'5'/'6' = same as 0/1/2 but also increments retract counter
        sh: '0' = test shutter open/close during init (default)
            '1' = skip shutter test
        cp: '0' = card pulled out during CAPTURE is not an error (default)
            '1' = card pulled out during CAPTURE is an error
        """
        text = (
            b"C" + b"0" + pm.encode() +
            b"0000000" +              # 7 fixed '0' bytes per spec
            sh.encode() +
            b"00" +                   # 2 fixed '0' bytes per spec
            cp.encode()
        )
        return self.link.send_command(text)

    def status_request(self, pm: str = "0") -> Response:
        """SCT STATUS REQUEST command (spec section 10.2). pm='0' or '1'."""
        text = b"C" + b"1" + pm.encode()
        return self.link.send_command(text)

    def enable(self) -> Response:
        """Enable card acceptance (spec section 10.7, pm='0')."""
        text = b"C" + b":" + b"0"   # cm=3AH=':' , pm=30H='0'
        return self.link.send_command(text)

    def disable(self) -> Response:
        """Disable card acceptance (spec section 10.7, pm='1')."""
        text = b"C" + b":" + b"1"
        return self.link.send_command(text)

    def eject(self) -> Response:
        """Move card from inside SCT to the Gate (spec section 10.4, pm='0')."""
        text = b"C" + b"3" + b"0"   # cm=33H='3', pm=30H='0'
        return self.link.send_command(text)

    def capture(self) -> Response:
        """Capture card to reject-stacker (spec section 10.4, pm='1')."""
        text = b"C" + b"3" + b"1"
        return self.link.send_command(text)

    def retrieve(self) -> Response:
        """Move card from Gate back to comms position (spec 10.5, pm='0')."""
        text = b"C" + b"4" + b"0"   # cm=34H='4', pm=30H='0'
        return self.link.send_command(text)

    def card_set_from_hopper(self) -> Response:
        """Feed one card from hopper to transport (spec section 10.3, pm='2')."""
        text = b"C" + b"2" + b"2"   # cm=32H='2', pm=32H='2'
        return self.link.send_command(text)


def describe_error(area: str, code: str) -> str:
    """
    area: 'ccb' or 'sct' - selects which error table to use.
    code: 2-character error code string, e.g. "A0", "10", "02".
    """
    common = {
        "00": "Given command code is unidentified",
        "01": "Parameter is not correct",
        "02": "Command execution is impossible",
        "04": "Command data error",
        "70": "F-ROM write failure",
        "71": "CRC error of User program code area",
    }
    sct_specific = {
        "10": "Card jam",
        "11": "Shutter failure",
        "12": "Sensor failure / card remains inside",
        "13": "Irregular card length (LONG)",
        "14": "Irregular card length (SHORT)",
        "16": "Card was moved forcibly",
        "17": "Jam error at retrieve",
        "18": "SW1 or SW2 error",
        "40": "Card was pulled out during capture",
        "45": "SCT ejected the card forcibly",
        "46": "Ejected card not withdrawn in time",
        "50": "Retract counter overflow",
        "A0": "No card at the hopper",
        "A5": "Card jam at the hopper",
        "A6": "Hopping kicker could not return home",
        "B0": "Command received before Initialize",
        "SA": "Under Supervisor program code area",
    }
    ccb_specific = {
        "62": "Protocol disagreement with activated card",
        "63": "No response from contactless IC card",
        "64": "Communication failure with contactless IC card",
        "65": "Contactless IC card is not activated",
        "A3": "Communication error to SCT",
    }
    table = dict(common)
    table.update(sct_specific if area == "sct" else ccb_specific)
    return table.get(code, f"Unknown error code {code!r}")
