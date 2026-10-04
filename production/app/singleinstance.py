"""Single-process guard (GAP-16).

Only one backend process may run at a time: two backends would both receive
every MQTT message and double-insert readings / double-fire alerts (the
dedupe caches are per-process). On Windows the primary guard is a NAMED
MUTEX — CreateMutex is atomic, so two processes started in the same instant
cannot both slip through (the old lockfile check had a read-then-write race
that allowed exactly that). The lockfile is kept as a human-readable record
of the holder PID; stale locks left by a killed process are detected via the
dead PID and overwritten.
"""

import os
import sys

LOCK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "backend.lock",
)

MUTEX_NAME = "IoTProductionBackendMutex"


def _acquire_windows_mutex():
    """Return the mutex handle, or None if another live backend holds it."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.restype = wintypes.HANDLE
    create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

    # Try the global namespace first (works across RDP/AnyDesk sessions on the
    # SGU server); fall back to the session-local namespace without privilege.
    for name in (f"Global\\{MUTEX_NAME}", f"Local\\{MUTEX_NAME}"):
        handle = create_mutex(None, False, name)
        err = ctypes.get_last_error()
        if handle and err == 0:
            return handle
        if handle:
            kernel32.CloseHandle(handle)
        if err != 5:  # not a privilege problem: someone else holds it
            return None
    return None


def _pid_alive(pid):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        query_limited = 0x0400
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            synchronize | query_limited, False, pid
        )
        if not handle:
            return False
        exit_code = wintypes.DWORD()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        # OpenProcess can succeed on a TERMINATED process whose handles are
        # still held open; only STILL_ACTIVE means the process is running.
        return bool(ok) and exit_code.value == still_active
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock():
    """Exit immediately if another live backend holds the lock; otherwise
    take it and return a release() callable for clean shutdown."""
    if sys.platform == "win32":
        handle = _acquire_windows_mutex()
        if not handle:
            print("Another backend is already running (mutex held). Exiting.")
            sys.exit(1)
        # Mutex held for the process lifetime; the handle must never close.

    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH) as f:
                pid = int(f.read().strip())
        except (ValueError, OSError):
            pid = None
        if pid and pid != os.getpid() and _pid_alive(pid):
            # Mutex says we're the owner, but a live process recorded here means
            # something is inconsistent — refuse to run rather than risk doubles.
            print(f"Another backend is already running (PID {pid}). Exiting.")
            sys.exit(1)

    with open(LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))

    my_pid = str(os.getpid())

    def release():
        try:
            with open(LOCK_PATH) as f:
                holder = f.read().strip()
            # Remove only after closing the handle — Windows refuses to delete
            # a file that is still open (and the OSError must not be swallowed
            # silently here or the lock outlives the process).
            if holder == my_pid:
                os.remove(LOCK_PATH)
        except OSError as e:
            print(f"Could not release backend lock: {e}")

    return release
