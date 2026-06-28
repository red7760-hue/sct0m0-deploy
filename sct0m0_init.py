"""
sct0m0_init.py
----------------------------------------------------------------------
Connects to a NIDEC SANKYO SCT0M0-0130 device over RS-232 using the
vendor-supplied SCT0M0_0130DLL.dll, and verifies the connection.

IMPORTANT - READ THIS FIRST
----------------------------------------------------------------------
1. SCT0M0_0130DLL.dll is a 32-BIT DLL. You must run this script with
   a 32-bit Python interpreter, or LoadLibrary will fail with
   "WinError 193: %1 is not a valid Win32 application".
   Check your Python build with:  python -c "import struct; print(struct.calcsize('P')*8)"
   It must print 32, not 64.

2. This script covers the parts of the API we can verify from the
   header/DLL alone:
       - ConnectDevice   (opens the COM port; this performs the
                           RS-232 "initiation" handshake internally)
       - GetDllInformation (sanity check that the DLL loaded correctly)
       - DisconnectDevice (clean shutdown)

3. There is NO separate public "Initialize" command exported by this
   DLL. The RS-232 line initiation (InitiatePrtclRS8) happens
   automatically inside ConnectDevice - you don't call it directly,
   and it isn't exported for you to call.

4. Sending an actual device command (e.g. a real "initialize the
   mechanism" command some devices need post-connect) requires
   bCommandCode / bParameterCode values that are NOT in this header.
   Those come from NIDEC SANKYO's command/protocol reference manual
   for this device. A placeholder for that call is included below,
   clearly marked, but it is NOT filled in with real values because
   I don't have that manual and guessing risks sending an unintended
   command to the hardware.
----------------------------------------------------------------------
"""

import ctypes
from ctypes import wintypes
import sys

# ----------------------------------------------------------------------
# Config - edit these for your setup
# ----------------------------------------------------------------------
DLL_PATH = r"SCT0M0_0130DLL.dll"   # full path if not alongside this script
COM_PORT = "COM7"
BAUD_RATE = 115200
COMMAND_TIMEOUT_MS = 5000           # used only if you fill in ExecuteCommand later

MAX_FNAME = 256          # matches _MAX_FNAME from <stdlib.h> on Windows
MAX_DATA_ARRAY_SIZE = 1024

# ----------------------------------------------------------------------
# Struct definitions - mirror SCT0M0_0130DLL.h exactly, packed to 8
# as the header specifies (#pragma pack(8)). On a 32-bit build,
# pointers and DWORDs are 4 bytes, so pack(8) doesn't change layout
# here, but we set it explicitly to match the header's intent.
# ----------------------------------------------------------------------

class _DllBlock(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("szFilename", ctypes.c_char * MAX_FNAME),
        ("szRevision", ctypes.c_char * 32),
    ]

class DLL_INFORMATION(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("upperDll", _DllBlock),
        ("lowerDll", _DllBlock),
    ]

class _CommandData(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("lpbBody", ctypes.POINTER(ctypes.c_ubyte)),
    ]

class COMMAND(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("bCommandTag", ctypes.c_ubyte),
        ("bCommandCode", ctypes.c_ubyte),
        ("bParameterCode", ctypes.c_ubyte),
        ("Data", _CommandData),
    ]

class _StatusCode(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("bSt1", ctypes.c_ubyte),
        ("bSt0", ctypes.c_ubyte),
        ("bSt2", ctypes.c_ubyte),
    ]

class _PositiveBody(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("bBody", ctypes.c_ubyte * MAX_DATA_ARRAY_SIZE),
    ]

class POSITIVE_REPLY(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("bReplyTag", ctypes.c_ubyte),
        ("bCommandCode", ctypes.c_ubyte),
        ("bParameterCode", ctypes.c_ubyte),
        ("StatusCode", _StatusCode),
        ("Data", _PositiveBody),
    ]

class _ErrorCode(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("bE1", ctypes.c_ubyte),
        ("bE0", ctypes.c_ubyte),
    ]

class _NegativeBody(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("bBody", ctypes.c_ubyte * MAX_DATA_ARRAY_SIZE),
    ]

class NEGATIVE_REPLY(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("bReplyTag", ctypes.c_ubyte),
        ("bCommandCode", ctypes.c_ubyte),
        ("bParameterCode", ctypes.c_ubyte),
        ("ErrorCode", _ErrorCode),
        ("Data", _NegativeBody),
    ]

class _ReplyUnion(ctypes.Union):
    _pack_ = 8
    _fields_ = [
        ("positiveReply", POSITIVE_REPLY),
        ("negativeReply", NEGATIVE_REPLY),
    ]

# REPLY_TYPE enum values, from the header
REPLY_TYPE_NAMES = {
    0: "PositiveReply",
    1: "NegativeReply",
    2: "ReplyReceivingFailure",
    3: "CommandCancellation",
    4: "ReplyTimeout",
}

class REPLY(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("replyType", ctypes.c_int),   # C enum -> int
        ("message", _ReplyUnion),
    ]

# ----------------------------------------------------------------------
# Error code lookup (from SCT0M0_0130DLL.h)
# ----------------------------------------------------------------------
ERROR_CODES = {
    0x0: "_NO_ERROR",
    0x1: "_DEVICE_NOT_CONNECTED_ERROR",
    0x2: "_CANCEL_COMMAND_SESSION_ERROR",
    0x3: "_FAILED_TO_SEND_COMMAND_ERROR",
    0x4: "_FAILED_TO_RECEIVE_REPLY_ERROR",
    0x5: "_COMMAND_CANCELED",
    0x6: "_REPLY_TIMEOUT",
    0x0101: "_CANNOT_CREATE_OBJECT_ERROR",
    0x0102: "_DEVICE_NOT_READY_ERROR",
    0x0103: "_CANNOT_OPEN_PORT_ERROR",
    0x0104: "_FAILED_TO_BEGIN_THREAD_ERROR",
    0x0105: "_DEVICE_ALREADY_CONNECTED_ERROR",
}

def describe_error(code: int) -> str:
    return ERROR_CODES.get(code, f"UNKNOWN_ERROR_0x{code:X}")


def load_dll(path: str):
    """Load the DLL, with a friendly error if it's a bitness mismatch."""
    try:
        return ctypes.WinDLL(path)
    except OSError as e:
        if getattr(e, "winerror", None) == 193:
            raise SystemExit(
                "ERROR: Could not load the DLL (WinError 193).\n"
                "This almost always means a 32-bit / 64-bit mismatch:\n"
                f"  - {path} is a 32-bit DLL.\n"
                "  - You are likely running a 64-bit Python interpreter.\n"
                "Fix: install/use a 32-bit Python (e.g. from python.org,\n"
                "the Windows x86 installer) and re-run this script with it."
            )
        raise


def main():
    if sys.platform != "win32":
        raise SystemExit("This script must be run on Windows (uses WinDLL).")

    dll = load_dll(DLL_PATH)

    # ---- Bind function prototypes -----------------------------------
    dll.GetDllInformation.argtypes = [ctypes.POINTER(DLL_INFORMATION)]
    dll.GetDllInformation.restype = wintypes.DWORD

    dll.ConnectDevice.argtypes = [ctypes.c_char_p, wintypes.DWORD]
    dll.ConnectDevice.restype = wintypes.DWORD

    dll.DisconnectDevice.argtypes = [ctypes.c_char_p]
    dll.DisconnectDevice.restype = wintypes.DWORD

    dll.ExecuteCommand2.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(COMMAND),
        wintypes.DWORD,
        ctypes.POINTER(REPLY),
    ]
    dll.ExecuteCommand2.restype = wintypes.DWORD

    port_bytes = COM_PORT.encode("ascii")

    # ---- Step 1: GetDllInformation (sanity check before connecting) --
    info = DLL_INFORMATION()
    rc = dll.GetDllInformation(ctypes.byref(info))
    print(f"[GetDllInformation] rc={rc} ({describe_error(rc)})")
    if rc == 0:
        print(f"  upperDll: {info.upperDll.szFilename.decode(errors='replace')} "
              f"rev {info.upperDll.szRevision.decode(errors='replace')}")
        print(f"  lowerDll: {info.lowerDll.szFilename.decode(errors='replace')} "
              f"rev {info.lowerDll.szRevision.decode(errors='replace')}")

    # ---- Step 2: ConnectDevice ---------------------------------------
    # This opens COM_PORT at BAUD_RATE and performs the RS-232 line
    # initiation handshake internally (InitiatePrtclRS8). This *is*
    # the device "initialization" step at this API's level.
    print(f"\n[ConnectDevice] Connecting to {COM_PORT} @ {BAUD_RATE} baud...")
    rc = dll.ConnectDevice(port_bytes, wintypes.DWORD(BAUD_RATE))
    print(f"[ConnectDevice] rc={rc} ({describe_error(rc)})")

    if rc != 0:
        print("Connection failed - stopping before attempting any commands.")
        return

    try:
        # ---------------------------------------------------------
        # PLACEHOLDER: real device "initialize" command would go here.
        #
        # Uncomment and fill in bCommandCode / bParameterCode once you
        # have NIDEC SANKYO's command reference for this device model.
        # Sending the wrong byte values to real hardware can trigger
        # an unintended physical action, so do NOT guess these.
        #
        # cmd = COMMAND()
        # cmd.bCommandTag = 0x02          # placeholder - confirm from manual
        # cmd.bCommandCode = 0x00         # placeholder - confirm from manual
        # cmd.bParameterCode = 0x00       # placeholder - confirm from manual
        # cmd.Data.dwSize = 0
        # cmd.Data.lpbBody = None
        #
        # reply = REPLY()
        # rc = dll.ExecuteCommand2(
        #     port_bytes, ctypes.byref(cmd), COMMAND_TIMEOUT_MS, ctypes.byref(reply)
        # )
        # print(f"[ExecuteCommand2] rc={rc} ({describe_error(rc)})")
        # print(f"  replyType={REPLY_TYPE_NAMES.get(reply.replyType, reply.replyType)}")
        # if reply.replyType == 0:  # PositiveReply
        #     pr = reply.message.positiveReply
        #     print(f"  StatusCode: St1={pr.StatusCode.bSt1:#x} "
        #           f"St0={pr.StatusCode.bSt0:#x} St2={pr.StatusCode.bSt2:#x}")
        # elif reply.replyType == 1:  # NegativeReply
        #     nr = reply.message.negativeReply
        #     print(f"  ErrorCode: E1={nr.ErrorCode.bE1:#x} E0={nr.ErrorCode.bE0:#x}")
        # ---------------------------------------------------------

        print("\nConnected successfully. No device command sent "
              "(command codes are not available - see header comment).")

    finally:
        # ---- Step 3: DisconnectDevice ---------------------------------
        print(f"\n[DisconnectDevice] Disconnecting {COM_PORT}...")
        rc = dll.DisconnectDevice(port_bytes)
        print(f"[DisconnectDevice] rc={rc} ({describe_error(rc)})")


if __name__ == "__main__":
    main()
