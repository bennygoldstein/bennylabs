"""Taskbar Sliders, macOS and Linux edition: volume, brightness and blue light.

A small borderless strip that stays on top, bottom right of the screen.
Python 3 + tkinter + standard library only.
"""
import ctypes
import glob
import hashlib
import http.client
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

try:
    import tkinter as tk
except ImportError:          # logic stays importable without tkinter
    tk = None

VERSION = "1"
SCRIPT = os.path.abspath(__file__)
HERE = os.path.dirname(SCRIPT)
SETTINGS = os.path.join(HERE, "settings.json")
STATE = os.path.join(HERE, "update_state.json")
LOGFILE = os.path.join(HERE, "sliders.log")
LOCKFILE = os.path.join(HERE, "sliders.lock")
LOG_LIMIT = 200 * 1024               # trimmed well below the 256 KB cap

DEFAULT_URL = "https://bennylabs.org/my-prompts/sliders/latest.json"
INTERVAL = 12 * 3600
FIRST_CHECK = 120
WATCHDOG_WAIT = 90
CONFIRM_AFTER = 30

COL_VOL, COL_BRIGHT, COL_BLUE = "#2e9e6b", "#e0a100", "#2f7de1"
BLUE_MAX = 50                        # far right removes 50% of blue
TOP = {"vol": 100, "bright": 100, "blue": BLUE_MAX}
BLOCK = 129                          # width of one slider block, in pixels at scale 1

DEFAULTS = {"auto_update": True, "update_url": "", "update_first_check": None,
            "x": None, "y": None, "bright": 100, "blue_removed": 0}

# --------------------------------------------------------------------------- log
_log_lock = threading.Lock()
_log_seen = {}


def log(msg):
    """Append a line to the log. The same text is not repeated within 30 s."""
    try:
        now = time.time()
        key = msg.splitlines()[0] if msg else ""
        with _log_lock:
            if now - _log_seen.get(key, 0) < 30:
                return
            _log_seen[key] = now
            try:
                if os.path.getsize(LOGFILE) > LOG_LIMIT:
                    with open(LOGFILE, "rb") as f:
                        f.seek(-100 * 1024, os.SEEK_END)
                        tail = f.read()
                    with open(LOGFILE, "wb") as f:
                        f.write(tail)
            except OSError:
                pass
            with open(LOGFILE, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg.rstrip() + "\n")
    except Exception:
        pass


def log_exc(where):
    log(where + ":\n" + traceback.format_exc())


# --------------------------------------------------------------------------- files
def write_json(path, data):
    """Atomic write: a temp file, then replace."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_settings():
    st = dict(DEFAULTS)
    st.update(read_json(SETTINGS))
    return st


def save_settings(st):
    try:
        write_json(SETTINGS, st)
    except OSError:
        log_exc("save settings")


_state_lock = threading.Lock()


def load_state():
    return read_json(STATE)


def update_state(**kw):
    with _state_lock:
        st = load_state()
        st.update(kw)
        write_json(STATE, st)
        return st


def acquire_lock(wait=0):
    """One running copy only. Returns the open lock file, or None when another copy has it."""
    f = open(LOCKFILE, "a+")
    try:
        import fcntl
    except ImportError:
        return f
    end = time.time() + wait
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.time() >= end:
                f.close()
                return None
            time.sleep(0.2)


# --------------------------------------------------------------------------- running commands
def run(cmd, timeout=5):
    """(returncode, stdout). (None, "") when the program is missing or hangs."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception:
        return None, ""


class Latest:
    """Runs fn(value) on a helper thread and only ever sends the newest value."""

    def __init__(self, fn):
        self.fn, self.cond, self.val, self.has, self.t = fn, threading.Condition(), None, False, None

    def push(self, v):
        with self.cond:
            self.val, self.has = v, True
            if self.t is None:
                self.t = threading.Thread(target=self._run, daemon=True)
                self.t.start()
            self.cond.notify()

    def _run(self):
        while True:
            with self.cond:
                while not self.has:
                    self.cond.wait()
                v, self.has = self.val, False
            try:
                self.fn(v)
            except Exception:
                log_exc("helper thread")


class Backend:
    """available(), get() -> percent or None, set(percent). apply() runs on the helper thread."""

    def __init__(self):
        self._w = Latest(self.apply)

    def available(self):
        return False

    def get(self):
        return None

    def set(self, p):
        self._w.push(p)

    def apply(self, p):
        pass

    def reapply(self):
        pass


def blue_gains(removed):
    """removed = percent of blue taken away. Returns (green, blue) as fractions kept."""
    b = 1 - removed / 100.0
    return 1 - (1 - b) * 0.3, b


# --------------------------------------------------------------------------- macOS
CG_PATH = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
DS_PATH = "/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices"


def load_cg():
    try:
        cg = ctypes.CDLL(CG_PATH)
        u32 = ctypes.c_uint32
        cg.CGGetActiveDisplayList.argtypes = [u32, ctypes.POINTER(u32), ctypes.POINTER(u32)]
        cg.CGGetActiveDisplayList.restype = ctypes.c_int32
        cg.CGDisplayIsBuiltin.argtypes = [u32]
        cg.CGDisplayIsBuiltin.restype = ctypes.c_int32
        cg.CGSetDisplayTransferByFormula.argtypes = [u32] + [ctypes.c_float] * 9
        cg.CGSetDisplayTransferByFormula.restype = ctypes.c_int32
        cg.CGDisplayRestoreColorSyncSettings.argtypes = []
        cg.CGDisplayRestoreColorSyncSettings.restype = None
        return cg
    except Exception:
        return None


def cg_displays(cg):
    ids, n = (ctypes.c_uint32 * 16)(), ctypes.c_uint32()
    if cg.CGGetActiveDisplayList(16, ids, ctypes.byref(n)) != 0:
        return []
    return list(ids[:n.value])


class MacVolume(Backend):
    def available(self):
        return sys.platform == "darwin" and self.get() is not None

    def get(self):
        rc, out = run(["osascript", "-e", "output volume of (get volume settings)"])
        out = out.strip()
        return int(out) if rc == 0 and out.isdigit() else None

    def apply(self, p):
        cmd = ["osascript", "-e", "set volume output volume %d" % p]
        if p > 0:
            cmd += ["-e", "set volume without output muted"]
        if run(cmd)[0] != 0:
            log("volume command failed")


class MacBrightness(Backend):
    """Built-in display only, through the private DisplayServices framework."""

    def _setup(self):
        if not hasattr(self, "_lib"):
            self._lib = None
            try:
                cg, ds = load_cg(), ctypes.CDLL(DS_PATH)
                ds.DisplayServicesGetBrightness.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_float)]
                ds.DisplayServicesGetBrightness.restype = ctypes.c_int32
                ds.DisplayServicesSetBrightness.argtypes = [ctypes.c_uint32, ctypes.c_float]
                ds.DisplayServicesSetBrightness.restype = ctypes.c_int32
                built = [d for d in cg_displays(cg) if cg.CGDisplayIsBuiltin(d)]
                if built:
                    self._lib = (ds, built[0])
            except Exception:
                self._lib = None
        return self._lib

    def available(self):
        return sys.platform == "darwin" and self.get() is not None

    def get(self):
        lib = self._setup()
        if not lib:
            return None
        v = ctypes.c_float()
        try:
            if lib[0].DisplayServicesGetBrightness(lib[1], ctypes.byref(v)) != 0:
                return None
        except Exception:
            return None
        return int(round(v.value * 100))

    def apply(self, p):
        lib = self._setup()
        if lib and lib[0].DisplayServicesSetBrightness(lib[1], p / 100.0) != 0:
            log("brightness call failed")


class MacBlue(Backend):
    """Tints every active display. macOS resets it now and then, so reapply() is called every 5 s."""

    def __init__(self):
        Backend.__init__(self)
        self.cg, self.last = None, 0

    def available(self):
        if sys.platform != "darwin":
            return False
        self.cg = self.cg or load_cg()
        return bool(self.cg) and bool(cg_displays(self.cg))

    def set(self, p):
        self.last = p
        Backend.set(self, p)

    def reapply(self):
        if self.last > 0:
            self._w.push(self.last)

    def apply(self, p):
        self.cg = self.cg or load_cg()
        if p <= 0:
            self.cg.CGDisplayRestoreColorSyncSettings()
            return
        g, b = blue_gains(p)
        for d in cg_displays(self.cg):
            self.cg.CGSetDisplayTransferByFormula(d, 0, 1, 1, 0, g, 1, 0, b, 1)


# --------------------------------------------------------------------------- Linux
def parse_wpctl(out):
    """'Volume: 0.45' or 'Volume: 0.45 [MUTED]' -> 45"""
    m = re.search(r"Volume:\s*([0-9.]+)", out or "")
    return min(100, int(round(float(m.group(1)) * 100))) if m else None


def parse_percent(out):
    """pactl and amixer: first 'NN%' in the text."""
    m = re.search(r"(\d+)%", out or "")
    return min(100, int(m.group(1))) if m else None


def parse_brightnessctl(out):
    """brightnessctl -m prints 'name,class,current,45%,max'."""
    parts = (out or "").strip().split(",")
    return parse_percent(parts[3]) if len(parts) >= 4 else None


def parse_outputs(out):
    """Names of connected screens from 'xrandr --query'."""
    names = []
    for line in (out or "").splitlines():
        p = line.split()
        if len(p) > 1 and p[1] == "connected":
            names.append(p[0])
    return names


def on_wayland():
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def x11_outputs():
    if on_wayland() or not shutil.which("xrandr") or not os.environ.get("DISPLAY"):
        return []
    rc, out = run(["xrandr", "--query"])
    return parse_outputs(out) if rc == 0 else []


# tool -> (name, get command, parser, set command for a percent, unmute command)
VOLUME_TOOLS = (
    ("wpctl", ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], parse_wpctl,
     lambda p: ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "%d%%" % p],
     ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"]),
    ("pactl", ["pactl", "get-sink-volume", "@DEFAULT_SINK@"], parse_percent,
     lambda p: ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "%d%%" % p],
     ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"]),
    ("amixer", ["amixer", "get", "Master"], parse_percent,
     lambda p: ["amixer", "set", "Master", "%d%%" % p],
     ["amixer", "set", "Master", "unmute"]),
)


class LinuxVolume(Backend):
    def __init__(self):
        Backend.__init__(self)
        self.tool = None

    def available(self):
        for t in VOLUME_TOOLS:
            if shutil.which(t[0]):
                rc, out = run(t[1])
                if rc == 0 and t[2](out) is not None:
                    self.tool = t
                    return True
        return False

    def get(self):
        if not self.tool:
            return None
        rc, out = run(self.tool[1])
        return self.tool[2](out) if rc == 0 else None

    def apply(self, p):
        if run(self.tool[3](p))[0] != 0:
            log("volume command failed (%s)" % self.tool[0])
        elif p > 0:
            run(self.tool[4])


class LinuxBrightness(Backend):
    def __init__(self):
        Backend.__init__(self)
        self.mode, self.path, self.outs = None, None, []

    def available(self):
        if shutil.which("brightnessctl"):
            rc, out = run(["brightnessctl", "-m"])
            if rc == 0 and parse_brightnessctl(out) is not None:
                self.mode = "ctl"
                return True
        for d in sorted(glob.glob("/sys/class/backlight/*")):
            if os.access(os.path.join(d, "brightness"), os.W_OK) and os.path.exists(os.path.join(d, "max_brightness")):
                self.mode, self.path = "sys", d
                return True
        self.outs = x11_outputs()
        if self.outs:
            self.mode = "xrandr"
            return True
        return False

    def get(self):
        if self.mode == "ctl":
            rc, out = run(["brightnessctl", "-m"])
            return parse_brightnessctl(out) if rc == 0 else None
        if self.mode == "sys":
            try:
                with open(os.path.join(self.path, "brightness")) as f:
                    cur = int(f.read())
                with open(os.path.join(self.path, "max_brightness")) as f:
                    top = int(f.read())
                return int(round(cur * 100.0 / top))
            except (OSError, ValueError, ZeroDivisionError):
                return None
        if self.mode == "xrandr":
            rc, out = run(["xrandr", "--verbose"])
            m = re.search(r"Brightness:\s*([0-9.]+)", out or "")
            return int(round(float(m.group(1)) * 100)) if m else None
        return None

    def apply(self, p):
        p = max(1, p)                       # never switch the screen fully off
        if self.mode == "ctl":
            if run(["brightnessctl", "set", "%d%%" % p])[0] != 0:
                log("brightnessctl failed")
        elif self.mode == "sys":
            with open(os.path.join(self.path, "max_brightness")) as f:
                top = int(f.read())
            with open(os.path.join(self.path, "brightness"), "w") as f:
                f.write(str(max(1, int(round(top * p / 100.0)))))
        elif self.mode == "xrandr":
            for o in self.outs:
                run(["xrandr", "--output", o, "--brightness", "%.2f" % (p / 100.0)])


class LinuxBlue(Backend):
    """X11 only: gamma 1 : (1-(1-b)*0.3) : b on every connected screen."""

    def __init__(self):
        Backend.__init__(self)
        self.outs = []

    def available(self):
        self.outs = x11_outputs()
        return bool(self.outs)

    def apply(self, p):
        g, b = blue_gains(p)
        for o in self.outs:
            run(["xrandr", "--output", o, "--gamma", "1.000:%.3f:%.3f" % (g, b)])


def build_backends(plat=None):
    """Only the sliders this computer can really do."""
    plat = plat or sys.platform
    if plat == "darwin":
        names = (("vol", MacVolume), ("bright", MacBrightness), ("blue", MacBlue))
    elif plat.startswith("linux"):
        names = (("vol", LinuxVolume), ("bright", LinuxBrightness), ("blue", LinuxBlue))
    else:
        return {}
    found = {}
    for name, cls in names:
        try:
            b = cls()
            if b.available():
                found[name] = b
        except Exception:
            log_exc("backend " + name)
    return found


# --------------------------------------------------------------------------- icons (same drawing as the Windows edition)
def mix(a, b, t):
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join("%02x" % round(x + (y - x) * t) for x, y in zip(ca, cb))


def thick(s, n):
    return max(1, int(round(n * s)))


def draw_speaker(cv, cx, cy, color, level, s, muted=False):
    ox = cx - s * 3
    pts = [(-8, -3), (-4, -3), (0, -7), (0, 7), (-4, 3), (-8, 3)]
    cv.create_polygon([v for x, y in pts for v in (ox + s * x, cy + s * y)], fill=color, outline=color)
    if muted or level <= 0:
        for sign in (1, -1):
            cv.create_line(ox + s * 4, cy - s * 3 * sign, ox + s * 10, cy + s * 3 * sign,
                           width=thick(s, 2), capstyle="round", fill=color)
        return
    for r in (5, 8, 11, 14)[:math.ceil(level * 4)]:
        cv.create_arc(ox - s * r, cy - s * r, ox + s * r, cy + s * r, start=-50, extent=100,
                      style="arc", width=thick(s, 2), outline=color)


def draw_sun(cv, cx, cy, color, level, s, muted=False):
    r = s * 4
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline=color)
    rays = 4 * math.ceil(level * 4)
    end = 9 + 3 * level
    for i in range(rays):
        a = -math.pi / 2 + i * 2 * math.pi / rays
        cv.create_line(cx + math.cos(a) * s * 6.5, cy + math.sin(a) * s * 6.5,
                       cx + math.cos(a) * s * end, cy + math.sin(a) * s * end,
                       width=thick(s, 2 if rays <= 10 else 1), capstyle="round", fill=color)


def draw_bulb(cv, cx, cy, color, level, s, muted=False):
    color = mix("#b9cde4", color, level)
    r = s * 6.5
    gy = cy - s * 3
    cv.create_oval(cx - r, gy - r, cx + r, gy + r, fill=color, outline=color)
    cv.create_polygon(cx - s * 4.5, gy + s * 4, cx + s * 4.5, gy + s * 4,
                      cx + s * 3, cy + s * 6, cx - s * 3, cy + s * 6, fill=color, outline=color)
    rays = math.ceil(level * 9)
    end = 10 + 2 * level
    for i in range(rays):
        a = math.radians(-90 + (i - (rays - 1) / 2) * 27.5)
        cv.create_line(cx + math.cos(a) * s * 8.5, gy + math.sin(a) * s * 8.5,
                       cx + math.cos(a) * s * end, gy + math.sin(a) * s * end,
                       width=thick(s, 2), capstyle="round", fill=color)
    for dy in (7.5, 10):
        cv.create_line(cx - s * 2.5, cy + s * dy, cx + s * 2.5, cy + s * dy,
                       width=thick(s, 2), capstyle="round", fill=color)


ICONS = {"vol": (draw_speaker, COL_VOL), "bright": (draw_sun, COL_BRIGHT), "blue": (draw_bulb, COL_BLUE)}


# --------------------------------------------------------------------------- layout and wheel maths
def zone(i, s):
    """Track start and end x of slider number i."""
    return int(round((14 + BLOCK * i) * s)), int(round((113 + BLOCK * i) * s))


def strip_width(n, s):
    return int(round((BLOCK * n - 2 if n else 220) * s))


def block_index(x, n, s):
    return max(0, min(n - 1, int(x // (BLOCK * s))))


def wheel_notches(num, delta):
    """+1 per notch up (right), -1 per notch down. X11 sends Button-4/5, macOS and Windows send delta."""
    if num == 4:
        return 1
    if num == 5:
        return -1
    if not delta:
        return 0
    if abs(delta) >= 120:
        return int(delta / 120)
    return 1 if delta > 0 else -1


# --------------------------------------------------------------------------- auto update
def allowed_url(u):
    return isinstance(u, str) and (u.startswith("https://") or bool(re.match(r"^http://127\.0\.0\.1(:\d+)?(/|$)", u)))


def plat_key(plat=None):
    return "mac" if (plat or sys.platform) == "darwin" else "linux"


def pick_entry(manifest, key, state):
    """The manifest entry for this platform when it is a newer, allowed, not-bad version, else None."""
    try:
        e = manifest["files"][key]
        v = e["version"]
        if isinstance(v, bool) or not isinstance(v, int) or v <= int(VERSION):
            return None
        if v in [int(x) for x in state.get("bad", [])]:
            return None
        if not allowed_url(e["url"]) or not isinstance(e["sha256"], str):
            return None
        return e
    except (KeyError, TypeError, ValueError):
        return None


def verify_payload(data, entry):
    """True when the bytes match the hash, compile, and carry the VERSION line they promise."""
    try:
        if hashlib.sha256(data).hexdigest() != entry["sha256"].strip().lower():
            return False
        text = data.decode("utf-8")
        compile(text, "sliders_unix.py", "exec")
        return bool(re.search(r'^VERSION = "%d"[ \t]*$' % entry["version"], text, re.M))
    except (ValueError, SyntaxError, TypeError):
        return False


def next_delay(last, now, first):
    """Seconds to wait before the next check."""
    if not isinstance(last, (int, float)) or last > now or now - last > INTERVAL:
        return first
    return max(0.0, INTERVAL - (now - last))


def fetch(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": "ScreenSliders/" + VERSION})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("too big")
    return data


def check_once(url, key, state, get=fetch):
    """(version, bytes) of a verified newer program, or None."""
    entry = pick_entry(json.loads(get(url, 1 << 20).decode("utf-8")), key, state)
    if not entry:
        return None
    data = get(entry["url"], 2 << 20)
    return (entry["version"], data) if verify_payload(data, entry) else None


def updater(q, st):
    """Daemon thread. Never touches Tk: results go through the queue."""
    try:
        url = st.get("update_url") if allowed_url(st.get("update_url")) else DEFAULT_URL
        first = st.get("update_first_check")
        first = float(first) if isinstance(first, (int, float)) and not isinstance(first, bool) else FIRST_CHECK
        time.sleep(next_delay(load_state().get("last_check"), time.time(), first))
        while True:
            res = None
            try:
                res = check_once(url, plat_key(), load_state())
            except (OSError, ValueError, http.client.HTTPException):
                pass                                    # network trouble: stay quiet
            except Exception:
                log_exc("update check")
            update_state(last_check=time.time())
            if res:
                q.put(res)
                return
            time.sleep(INTERVAL)
    except Exception:
        log_exc("updater")


WATCHDOG = """
import sys, os, time, json, signal, shutil, subprocess
sp, script, ver, delay = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
time.sleep(delay)
try:
    with open(sp) as f:
        st = json.load(f)
except Exception:
    st = {}
p = st.get("pending")
if p and int(p.get("version", 0)) == ver:
    pid = int(p.get("pid") or 0)
    if pid > 1:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except Exception:
            pass
        time.sleep(1)
    shutil.copyfile(p["backup"], script)
    bad = [int(x) for x in st.get("bad", [])]
    if ver not in bad:
        bad.append(ver)
    st["bad"] = bad
    st["pending"] = None
    with open(sp + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(sp + ".tmp", sp)
    subprocess.Popen([sys.executable, script], start_new_session=True, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"""


def spawn(args):
    subprocess.Popen(args, start_new_session=True, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def free_name(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists("%s_%d%s" % (base, n, ext)):
        n += 1
    return "%s_%d%s" % (base, n, ext)


def apply_update(version, data, lock, script=None, wait=WATCHDOG_WAIT):
    """Swap in the new program, arm the watchdog, hand over to the new copy. The caller then exits."""
    script = script or SCRIPT
    backup = free_name(os.path.join(os.path.dirname(script), "sliders_unix_v%s.py" % VERSION))
    shutil.copy2(script, backup)
    tmp = script + ".new"
    swapped = False
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        update_state(pending={"version": version, "from": int(VERSION), "backup": backup, "pid": 0})
        os.replace(tmp, script)
        swapped = True
        spawn([sys.executable, "-I", "-c", WATCHDOG, STATE, script, str(version), str(wait)])
        if lock:
            lock.close()                                # releases the lock
        spawn([sys.executable, script])
    except Exception:
        if swapped:
            shutil.copyfile(backup, script)
        update_state(pending=None)
        raise


def note_start():
    """The new copy records its pid in pending."""
    p = load_state().get("pending")
    if p and int(p.get("version", 0)) == int(VERSION):
        update_state(pending=dict(p, pid=os.getpid()))


def confirm():
    p = load_state().get("pending")
    if p and int(p.get("version", 0)) == int(VERSION):
        update_state(pending=None)


# --------------------------------------------------------------------------- the strip
class Strip:
    def __init__(self, st, backends, lock):
        self.st, self.be, self.lock = st, backends, lock
        self.names = [n for n in ("vol", "bright", "blue") if n in backends]
        self.vals = {"vol": 0, "bright": int(st.get("bright", 100)),
                     "blue": max(0, min(BLUE_MAX, int(st.get("blue_removed", 0))))}
        for n in ("vol", "bright"):
            v = backends[n].get() if n in backends else None
            if v is not None:
                self.vals[n] = v
        if "blue" in backends and self.vals["blue"] > 0:
            backends["blue"].set(self.vals["blue"])
        self.active = self.drag = self.hover_x = self.hold = None
        self.touched = {"vol": 0.0, "bright": 0.0}
        self.polled = {}
        self.n = 0
        self.tip_on = False
        self.exited = False
        self.uq = queue.Queue()
        self.bg, self.fg, self.track = "#202020", "#f2f2f2", "#6a6a6a"

        self.root = r = tk.Tk()
        r.title("ScreenSliders")
        r.report_callback_exception = lambda t, v, tb: log("tk callback:\n" + "".join(traceback.format_exception(t, v, tb)))
        r.withdraw()
        r.overrideredirect(True)
        r.configure(bg=self.bg)
        try:
            r.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.S = 1.0 if sys.platform == "darwin" else max(1.0, min(3.0, r.winfo_fpixels("1i") / 96.0))
        self.W, self.H = strip_width(len(self.names), self.S), int(round(40 * self.S))
        self.cv = tk.Canvas(r, width=self.W, height=self.H, highlightthickness=0, bd=0, bg=self.bg)
        self.cv.pack()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        margin = 90 if sys.platform == "darwin" else 60          # above the Dock or the panel
        x = st["x"] if isinstance(st.get("x"), int) else sw - self.W - 12
        y = st["y"] if isinstance(st.get("y"), int) else sh - self.H - int(margin * self.S)
        self.move_to(x, y)
        r.deiconify()
        self.make_tip()
        for seq in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>", "<Motion>", "<Leave>",
                    "<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.cv.bind(seq, self.handler(seq))
        self.draw()
        threading.Thread(target=self.poll_loop, daemon=True).start()
        if st.get("auto_update", True):
            threading.Thread(target=updater, args=(self.uq, st), daemon=True).start()
        r.after(CONFIRM_AFTER * 1000, self.confirm_when_mapped)
        r.after(40, self.tick)

    def handler(self, seq):
        fn = {"<ButtonPress-1>": self.on_press, "<B1-Motion>": self.on_drag, "<ButtonRelease-1>": self.on_release,
              "<Motion>": self.on_motion, "<Leave>": self.on_leave}.get(seq, self.on_wheel)

        def call(e):
            self.safe(lambda: fn(e))
        return call

    def safe(self, fn):
        try:
            fn()
        except Exception:
            log_exc(getattr(fn, "__name__", "callback"))

    # -- window and tooltip
    def px(self, v):
        return int(round(v * self.S))

    def move_to(self, x, y):
        x = max(0, min(self.root.winfo_screenwidth() - self.W, x))
        y = max(0, min(self.root.winfo_screenheight() - self.H, y))
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, x, y))

    def make_tip(self):
        t = self.tip = tk.Toplevel(self.root)
        t.withdraw()
        t.overrideredirect(True)
        t.configure(bg="#2b2b2b")
        try:
            t.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.tip_label = tk.Label(t, text="100%", font=("Helvetica", -self.px(12)), bg="#2b2b2b", fg="#ffffff",
                                  padx=self.px(7), pady=self.px(3), bd=0)
        self.tip_label.pack()

    def slider_x(self, name):
        x0, x1 = zone(self.names.index(name), self.S)
        return x0 + (x1 - x0) * self.vals[name] / float(TOP[name])

    def tip_target(self):
        """The slider being dragged or wheeled, or the one whose knob is under the mouse."""
        if self.drag:
            return None
        if self.active:
            return self.active
        if self.hold and time.time() < self.hold[1]:
            return self.hold[0]
        if self.hover_x is not None:
            for n in self.names:
                if abs(self.hover_x - self.slider_x(n)) <= self.px(12):
                    return n
        return None

    def update_tip(self):
        name = self.tip_target()
        if not name:
            if self.tip_on:
                self.tip.withdraw()
                self.tip_on = False
            return
        self.tip_label.configure(text="%d%%" % self.vals[name])
        self.tip.update_idletasks()
        tw, th = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        y = ry - th - self.px(6)
        if y < 0:
            y = ry + self.H + self.px(6)
        self.tip.geometry("+%d+%d" % (max(0, rx + int(self.slider_x(name)) - tw // 2), y))
        if not self.tip_on:
            self.tip.deiconify()
            self.tip_on = True

    # -- drawing
    def draw(self):
        cv, mid, s = self.cv, self.H // 2, self.S
        cv.delete("all")
        if not self.names:
            cv.create_text(self.W // 2, mid, text="No sliders available here", fill=self.fg,
                           font=("Helvetica", -self.px(12)))
            return
        for i, name in enumerate(self.names):
            icon, color = ICONS[name]
            x0, x1 = zone(i, s)
            vx = self.slider_x(name)
            cv.create_line(x0, mid, x1, mid, width=self.px(4), capstyle="round", fill=self.track)
            cv.create_line(x0, mid, vx, mid, width=self.px(4), capstyle="round", fill=color)
            r = s * 12
            cv.create_oval(vx - r, mid - r, vx + r, mid + r, fill=self.bg, outline=self.bg)
            frac = self.vals[name] / float(TOP[name])
            if name == "vol":
                icon(cv, vx, mid, color, frac, s, muted=self.vals["vol"] == 0)
            else:
                icon(cv, vx, mid, color, frac, s)

    # -- values
    def set_value(self, name, v):
        v = max(0, min(TOP[name], int(v)))
        if v != self.vals[name]:
            self.vals[name] = v
            if name in self.touched:
                self.touched[name] = time.time()
            self.be[name].set(v)
        self.draw()

    def save(self):
        self.st["bright"] = self.vals["bright"]
        self.st["blue_removed"] = self.vals["blue"]
        self.st["x"], self.st["y"] = self.root.winfo_x(), self.root.winfo_y()
        save_settings(self.st)

    # -- input
    def hit(self, x):
        for i, name in enumerate(self.names):
            x0, x1 = zone(i, self.S)
            if x0 - self.px(10) <= x <= x1 + self.px(10):
                return name
        return None

    def set_from_x(self, name, x):
        x0, x1 = zone(self.names.index(name), self.S)
        f = max(0.0, min(1.0, (x - x0) / float(x1 - x0)))
        self.set_value(name, round(f * TOP[name]))

    def on_press(self, e):
        self.active = self.hit(e.x)
        if self.active:
            self.set_from_x(self.active, e.x)
        else:
            self.drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def on_drag(self, e):
        self.hover_x = e.x
        if self.active:
            self.set_from_x(self.active, e.x)
        elif self.drag:
            self.move_to(e.x_root - self.drag[0], e.y_root - self.drag[1])

    def on_release(self, e):
        self.active = self.drag = None
        self.save()

    def on_motion(self, e):
        self.hover_x = e.x

    def on_leave(self, e):
        self.hover_x = None

    def on_wheel(self, e):
        n = wheel_notches(getattr(e, "num", 0), getattr(e, "delta", 0))
        if not n or not self.names:
            return
        name = self.names[block_index(e.x, len(self.names), self.S)]
        self.set_value(name, self.vals[name] + 2 * n)          # wheel up moves right
        self.hold = (name, time.time() + 1.2)
        self.save()

    # -- timers
    def poll_loop(self):
        while True:
            time.sleep(3)
            for n in ("vol", "bright"):
                if n in self.be:
                    try:
                        v = self.be[n].get()
                    except Exception:
                        v = None
                    if v is not None:
                        self.polled[n] = v

    def take_polled(self):
        if self.active or self.drag:
            return
        for n, v in list(self.polled.items()):
            if time.time() - self.touched[n] > 4 and v != self.vals[n]:
                self.vals[n] = v
                self.draw()

    def slow(self):
        if "blue" in self.be:
            self.be["blue"].reapply()
        self.root.lift()

    def check_update(self):
        if self.active or self.drag or self.uq.empty():
            return
        version, data = self.uq.get()
        try:
            apply_update(version, data, self.lock)
        except Exception:
            log_exc("apply update")
            return
        self.exited = True
        self.root.destroy()

    def confirm_when_mapped(self):
        if self.exited:
            return
        if self.root.winfo_ismapped():
            self.safe(confirm)
        else:
            self.root.after(5000, self.confirm_when_mapped)

    def tick(self):
        self.n += 1
        self.safe(self.update_tip)
        if self.n % 25 == 0:
            self.safe(self.take_polled)
        if self.n % 125 == 0:
            self.safe(self.slow)
        self.safe(self.check_update)
        if not self.exited:
            self.root.after(40, self.tick)


def main():
    lock = acquire_lock(wait=5)
    if lock is None:
        return 0
    if tk is None:
        log("tkinter is not installed")
        return 1
    try:
        note_start()
        st = load_settings()
        strip = Strip(st, build_backends(), lock)
        strip.root.mainloop()
    except Exception:
        log_exc("main")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
