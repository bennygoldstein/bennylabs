"""ScreenSliders: volume, brightness and blue-light sliders on the Windows 11 taskbar.

Python 3 + tkinter + ctypes + standard library only.
"""
import ctypes
import hashlib
import json
import math
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import winreg
import zlib
from ctypes import wintypes as w
import tkinter as tk

VERSION = "6"
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "settings.json")
LOGFILE = os.path.join(HERE, "screen_sliders.log")
LOG_LIMIT = 200 * 1024          # trimmed well below the 256 KB cap
MUTEX_NAME = "ScreenSlidersSingleInstance"
TRAY_TIP = "Screen Sliders"
NOTIFY_KEY = r"Control Panel\NotifyIconSettings"

COL_VOL, COL_BRIGHT, COL_BLUE = "#2e9e6b", "#e0a100", "#2f7de1"

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


def _excepthook(t, v, tb):
    log("uncaught:\n" + "".join(traceback.format_exception(t, v, tb)))


sys.excepthook = _excepthook
try:
    threading.excepthook = lambda a: log("thread error:\n" + "".join(
        traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
except Exception:
    pass

# --------------------------------------------------------------------------- win32
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
user = ctypes.WinDLL("user32", use_last_error=True)
gdi = ctypes.WinDLL("gdi32", use_last_error=True)
dxva = ctypes.WinDLL("dxva2", use_last_error=True)
shell = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")
oleaut = ctypes.WinDLL("oleaut32")


class RECT(ctypes.Structure):
    _fields_ = [("l", w.LONG), ("t", w.LONG), ("r", w.LONG), ("b", w.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG)]


class MI(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("rc", RECT), ("wk", RECT), ("fl", w.DWORD), ("dev", w.WCHAR * 32)]


class PM(ctypes.Structure):
    _fields_ = [("h", w.HANDLE), ("desc", w.WCHAR * 128)]


class GUID(ctypes.Structure):
    _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", ctypes.c_ubyte * 8)]


class NID(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                ("uCallbackMessage", w.UINT), ("hIcon", w.HANDLE), ("szTip", w.WCHAR * 128),
                ("dwState", w.DWORD), ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256),
                ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", w.HANDLE)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", w.BOOL), ("xHot", w.DWORD), ("yHot", w.DWORD), ("mask", w.HANDLE), ("color", w.HANDLE)]


class BMIH(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                ("biClrImportant", w.DWORD)]


MONENUM = ctypes.WINFUNCTYPE(w.BOOL, w.HMONITOR, w.HDC, ctypes.POINTER(RECT), w.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, w.HANDLE, w.DWORD, w.HWND, w.LONG, w.LONG, w.DWORD, w.DWORD)


def _d(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = list(argtypes)


_d(user.EnumDisplayMonitors, w.BOOL, w.HDC, ctypes.c_void_p, MONENUM, w.LPARAM)
_d(user.GetMonitorInfoW, w.BOOL, w.HMONITOR, ctypes.POINTER(MI))
_d(user.MonitorFromWindow, w.HMONITOR, w.HWND, w.DWORD)
_d(user.FindWindowW, w.HWND, w.LPCWSTR, w.LPCWSTR)
_d(user.FindWindowExW, w.HWND, w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR)
_d(user.GetWindowRect, w.BOOL, w.HWND, ctypes.POINTER(RECT))
_d(user.GetAncestor, w.HWND, w.HWND, w.UINT)
_d(user.GetForegroundWindow, w.HWND)
_d(user.SetForegroundWindow, w.BOOL, w.HWND)
_d(user.GetClassNameW, ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
_d(user.GetDpiForWindow, w.UINT, w.HWND)
_d(user.SetWindowPos, w.BOOL, w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT)
_d(user.ShowWindow, w.BOOL, w.HWND, ctypes.c_int)
_d(user.GetWindowLongPtrW, ctypes.c_ssize_t, w.HWND, ctypes.c_int)
_d(user.SetWindowLongPtrW, ctypes.c_ssize_t, w.HWND, ctypes.c_int, ctypes.c_ssize_t)
_d(user.CallWindowProcW, ctypes.c_ssize_t, ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
_d(user.GetDC, w.HDC, w.HWND)
_d(user.ReleaseDC, ctypes.c_int, w.HWND, w.HDC)
_d(user.GetWindowThreadProcessId, w.DWORD, w.HWND, ctypes.POINTER(w.DWORD))
_d(user.SetWinEventHook, w.HANDLE, w.DWORD, w.DWORD, w.HMODULE, WINEVENTPROC, w.DWORD, w.DWORD, w.DWORD)
_d(user.UnhookWinEvent, w.BOOL, w.HANDLE)
_d(user.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_d(user.ScreenToClient, w.BOOL, w.HWND, ctypes.POINTER(POINT))
_d(user.CreateIconIndirect, w.HANDLE, ctypes.POINTER(ICONINFO))
_d(user.DestroyIcon, w.BOOL, w.HANDLE)
_d(gdi.CreateDCW, w.HDC, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p)
_d(gdi.DeleteDC, w.BOOL, w.HDC)
_d(gdi.SetDeviceGammaRamp, w.BOOL, w.HDC, ctypes.c_void_p)
_d(gdi.GetDeviceGammaRamp, w.BOOL, w.HDC, ctypes.c_void_p)
_d(gdi.GetPixel, w.DWORD, w.HDC, ctypes.c_int, ctypes.c_int)
_d(gdi.CreateDIBSection, w.HANDLE, w.HDC, ctypes.c_void_p, w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD)
_d(gdi.CreateBitmap, w.HANDLE, ctypes.c_int, ctypes.c_int, w.UINT, w.UINT, ctypes.c_void_p)
_d(gdi.DeleteObject, w.BOOL, w.HANDLE)
_d(shell.Shell_NotifyIconW, w.BOOL, w.DWORD, ctypes.POINTER(NID))
_d(kernel.CreateMutexW, w.HANDLE, ctypes.c_void_p, w.BOOL, w.LPCWSTR)
_d(kernel.CloseHandle, w.BOOL, w.HANDLE)
_d(user.IsWindowVisible, w.BOOL, w.HWND)
_d(ole32.CoInitializeEx, ctypes.c_long, ctypes.c_void_p, w.DWORD)
_d(ole32.CoCreateInstance, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
_d(ole32.CoSetProxyBlanket, ctypes.c_long, ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD)
_d(oleaut.SysAllocString, ctypes.c_void_p, w.LPCWSTR)
_d(oleaut.SysFreeString, None, ctypes.c_void_p)
_d(oleaut.VariantClear, ctypes.c_long, ctypes.c_void_p)
_d(dxva.GetNumberOfPhysicalMonitorsFromHMONITOR, w.BOOL, w.HMONITOR, ctypes.POINTER(w.DWORD))
_d(dxva.GetPhysicalMonitorsFromHMONITOR, w.BOOL, w.HMONITOR, w.DWORD, ctypes.POINTER(PM))
_d(dxva.DestroyPhysicalMonitors, w.BOOL, w.DWORD, ctypes.POINTER(PM))
_d(dxva.SetVCPFeature, w.BOOL, w.HANDLE, w.BYTE, w.DWORD)
_d(dxva.GetVCPFeatureAndVCPFeatureReply, w.BOOL, w.HANDLE, w.BYTE, ctypes.POINTER(w.DWORD),
   ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD))

HWND_TOPMOST = w.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10


def set_dpi_aware():
    try:
        user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):   # per-monitor v2
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def monitors():
    found = []

    def cb(h, _dc, _rc, _lp):
        found.append(h)
        return True

    cbf = MONENUM(cb)
    user.EnumDisplayMonitors(None, None, cbf, 0)
    return found


def monitor_device(h):
    mi = MI()
    mi.cb = ctypes.sizeof(MI)
    user.GetMonitorInfoW(h, ctypes.byref(mi))
    return mi.dev


def physical(h):
    n = w.DWORD()
    if not dxva.GetNumberOfPhysicalMonitorsFromHMONITOR(h, ctypes.byref(n)) or not n.value:
        return None, 0
    arr = (PM * n.value)()
    if not dxva.GetPhysicalMonitorsFromHMONITOR(h, n.value, arr):
        return None, 0
    return arr, n.value


# --------------------------------------------------------------------------- blue light
def make_ramp(removed):
    """removed = percent of blue taken away (0..100)."""
    b = 1 - removed / 100.0
    ramp = (w.WORD * 768)()
    for i in range(256):
        v = i * 257
        ramp[i] = v
        ramp[256 + i] = int(v * (1 - (1 - b) * 0.3))
        ramp[512 + i] = int(v * b)
    return ramp


def set_blue(removed):
    """Apply the tint to every screen. True if every screen accepted it."""
    ramp = make_ramp(removed)
    ok = True
    for h in monitors():
        dc = gdi.CreateDCW(monitor_device(h), None, None, None)
        if dc:
            ok = bool(gdi.SetDeviceGammaRamp(dc, ramp)) and ok
            gdi.DeleteDC(dc)
        else:
            ok = False
    return ok


def ramp_matches(removed, tol=2):
    """True when every screen's current ramp is within tol of the wanted one."""
    want = make_ramp(removed)
    for h in monitors():
        dc = gdi.CreateDCW(monitor_device(h), None, None, None)
        if not dc:
            continue
        cur = (w.WORD * 768)()
        got = gdi.GetDeviceGammaRamp(dc, cur)
        gdi.DeleteDC(dc)
        if got and any(abs(cur[i] - want[i]) > tol for i in range(768)):
            return False
    return True


def find_blue_floor():
    """Windows refuses very strong tints: find the lowest percent of blue it accepts."""
    floor = 100
    for kept in range(40, 100, 2):
        if set_blue(100 - kept):
            floor = kept
            break
    set_blue(0)
    return floor


# --------------------------------------------------------------------------- COM helpers
def guid_buf(text):
    return ctypes.create_string_buffer(uuid.UUID(text).bytes_le, 16)


def com_method(obj, index, restype, *argtypes):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])
    return lambda *args: fn(obj, *args)


def com_release(obj):
    if obj and obj.value:
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(obj)


def hr_check(hr, what):
    if hr < 0:
        raise OSError("%s failed 0x%08X" % (what, hr & 0xFFFFFFFF))
    return hr


# --------------------------------------------------------------------------- volume
def _with_speakers(action):
    """Run action(IAudioEndpointVolume) on whatever the default speakers are right now."""
    enum, dev, vol = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    hr_check(ole32.CoCreateInstance(guid_buf("BCDE0395-E52F-467C-8E3D-C4579291692E"), None, 23,
                                    guid_buf("A95664D2-9614-4F35-A746-DE8DB63617E6"), ctypes.byref(enum)), "enumerator")
    try:
        hr_check(com_method(enum, 4, ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)(
            0, 0, ctypes.byref(dev)), "default endpoint")
        try:
            hr_check(com_method(dev, 3, ctypes.c_long, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, ctypes.c_void_p)(
                guid_buf("5CDF2C82-841E-4546-9722-0CF74078229A"), 23, None, ctypes.byref(vol)), "activate")
            try:
                return action(vol)
            finally:
                com_release(vol)
        finally:
            com_release(dev)
    finally:
        com_release(enum)


def read_volume():
    """(percent, muted) or None when there are no speakers."""
    def action(vol):
        level, mute = ctypes.c_float(), w.BOOL()
        hr_check(com_method(vol, 9, ctypes.c_long, ctypes.c_void_p)(ctypes.byref(level)), "get volume")
        hr_check(com_method(vol, 15, ctypes.c_long, ctypes.c_void_p)(ctypes.byref(mute)), "get mute")
        return int(round(level.value * 100)), bool(mute.value)
    try:
        return _with_speakers(action)
    except OSError as e:
        log("read_volume: %s" % e)
        return None


def write_volume(percent):
    def action(vol):
        hr_check(com_method(vol, 7, ctypes.c_long, ctypes.c_float, ctypes.c_void_p)(percent / 100.0, None), "set volume")
        if percent > 0:   # like the Windows slider, any increase above 0 un-mutes
            hr_check(com_method(vol, 14, ctypes.c_long, w.BOOL, ctypes.c_void_p)(False, None), "set mute")
    try:
        _with_speakers(action)
    except OSError as e:
        log("write_volume: %s" % e)


# --------------------------------------------------------------------------- WMI (COM through ctypes)
class VARIANT(ctypes.Structure):
    _fields_ = [("vt", w.WORD), ("r1", w.WORD), ("r2", w.WORD), ("r3", w.WORD),
                ("v1", ctypes.c_longlong), ("v2", ctypes.c_longlong)]


VT_I4, VT_BSTR, VT_UI1 = 3, 8, 17


class Wmi:
    """A tiny IWbemServices client. Slots: ConnectServer 3; GetObject 6, ExecQuery 20, ExecMethod 24;
    Enum.Next 4; Object.Get 4, Put 5, SpawnInstance 15, GetMethod 19."""

    def __init__(self, namespace):
        self.loc, self.svc = ctypes.c_void_p(), ctypes.c_void_p()
        hr_check(ole32.CoCreateInstance(guid_buf("4590F811-1D3A-11D0-891F-00AA004B2E24"), None, 1,
                                        guid_buf("DC12A687-737F-11CF-884D-00AA004B2E24"), ctypes.byref(self.loc)), "WbemLocator")
        ns = oleaut.SysAllocString(namespace)
        try:
            hr_check(com_method(self.loc, 3, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p)(ns, None, None, None, 0, None, None, ctypes.byref(self.svc)), "ConnectServer")
        finally:
            oleaut.SysFreeString(ns)
        hr_check(ole32.CoSetProxyBlanket(self.svc, 10, 0, None, 3, 3, None, 0), "CoSetProxyBlanket")

    def close(self):
        com_release(self.svc)
        com_release(self.loc)

    def query(self, wql):
        """List of IWbemClassObject pointers (caller releases)."""
        lang, q = oleaut.SysAllocString("WQL"), oleaut.SysAllocString(wql)
        enum = ctypes.c_void_p()
        try:
            hr_check(com_method(self.svc, 20, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
                                ctypes.c_void_p, ctypes.c_void_p)(lang, q, 0x30, None, ctypes.byref(enum)), "ExecQuery")
        finally:
            oleaut.SysFreeString(lang)
            oleaut.SysFreeString(q)
        out = []
        try:
            nxt = com_method(enum, 4, ctypes.c_long, ctypes.c_long, w.ULONG, ctypes.c_void_p, ctypes.POINTER(w.ULONG))
            while True:
                obj, got = ctypes.c_void_p(), w.ULONG()
                nxt(-1, 1, ctypes.byref(obj), ctypes.byref(got))
                if not got.value:
                    break
                out.append(obj)
        finally:
            com_release(enum)
        return out

    @staticmethod
    def get(obj, name):
        v = VARIANT()
        hr_check(com_method(obj, 4, ctypes.c_long, w.LPCWSTR, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.c_void_p)(name, 0, ctypes.byref(v), None, None), "Get " + name)
        try:
            if v.vt == VT_BSTR:
                return ctypes.wstring_at(v.v1) if v.v1 else ""
            if v.vt in (VT_UI1,):
                return v.v1 & 0xFF
            return ctypes.c_int32(v.v1 & 0xFFFFFFFF).value
        finally:
            oleaut.VariantClear(ctypes.byref(v))

    @staticmethod
    def put_int(obj, name, value):
        v = VARIANT()
        v.vt, v.v1 = VT_I4, int(value)
        hr_check(com_method(obj, 5, ctypes.c_long, w.LPCWSTR, ctypes.c_long, ctypes.c_void_p, ctypes.c_long)(
            name, 0, ctypes.byref(v), 0), "Put " + name)

    def exec_method(self, path, method, in_obj):
        p, m = oleaut.SysAllocString(path), oleaut.SysAllocString(method)
        try:
            hr_check(com_method(self.svc, 24, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
                                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
                p, m, 0, None, in_obj, None, None), "ExecMethod")
        finally:
            oleaut.SysFreeString(p)
            oleaut.SysFreeString(m)

    def new_in_params(self, cls, method):
        c = ctypes.c_void_p()
        n = oleaut.SysAllocString(cls)
        try:
            hr_check(com_method(self.svc, 6, ctypes.c_long, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p)(n, 0, None, ctypes.byref(c), None), "GetObject")
        finally:
            oleaut.SysFreeString(n)
        sig, inst = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            hr_check(com_method(c, 19, ctypes.c_long, w.LPCWSTR, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
                method, 0, ctypes.byref(sig), None), "GetMethod")
            hr_check(com_method(sig, 15, ctypes.c_long, ctypes.c_long, ctypes.c_void_p)(0, ctypes.byref(inst)), "SpawnInstance")
        finally:
            com_release(sig)
            com_release(c)
        return inst


def wmi_read_brightness():
    """Brightness (0-100) of the first WMI-controllable panel, or None."""
    wm = Wmi(r"ROOT\WMI")
    try:
        objs = wm.query("SELECT CurrentBrightness FROM WmiMonitorBrightness WHERE Active=TRUE")
        try:
            return Wmi.get(objs[0], "CurrentBrightness") if objs else None
        finally:
            for o in objs:
                com_release(o)
    finally:
        wm.close()


def wmi_set_brightness(percent):
    """Set every WMI-controllable panel. Returns how many were set."""
    wm = Wmi(r"ROOT\WMI")
    done = 0
    try:
        objs = wm.query("SELECT * FROM WmiMonitorBrightnessMethods WHERE Active=TRUE")
        try:
            if not objs:
                return 0
            inst = wm.new_in_params("WmiMonitorBrightnessMethods", "WmiSetBrightness")
            try:
                Wmi.put_int(inst, "Timeout", 0)
                Wmi.put_int(inst, "Brightness", percent)
                for o in objs:
                    wm.exec_method(Wmi.get(o, "__PATH"), "WmiSetBrightness", inst)
                    done += 1
            finally:
                com_release(inst)
        finally:
            for o in objs:
                com_release(o)
    finally:
        wm.close()
    return done


# --------------------------------------------------------------------------- brightness worker
class BrightnessWorker(threading.Thread):
    """Owns all DDC/CI and WMI traffic. Reads once at startup, then sends only the newest value."""

    def __init__(self, results):
        super().__init__(daemon=True)
        self.results = results
        self.cond = threading.Condition()
        self.pending = None
        self.maxes = {}          # (hmonitor, index) -> reported maximum
        self.ddc_ok = {}         # hmonitor -> answered DDC/CI at startup
        self.need_wmi = False

    def push(self, value):
        with self.cond:
            self.pending = value
            self.cond.notify()

    def read_ddc(self, p):
        """Value that two reads in a row agree on (up to 4 reads), as (percent, max) or None."""
        prev = None
        for _ in range(4):
            cur, mx, t = w.DWORD(), w.DWORD(), w.DWORD()
            if dxva.GetVCPFeatureAndVCPFeatureReply(p.h, 0x10, ctypes.byref(t), ctypes.byref(cur), ctypes.byref(mx)) and mx.value:
                got = (cur.value, mx.value)
                if prev == got:
                    return got
                prev = got
            else:
                prev = None
            time.sleep(0.06)
        return None

    def startup_read(self):
        first = None
        for h in monitors():
            arr, n = physical(h)
            dev = monitor_device(h)
            if not arr:
                self.ddc_ok[h] = False
                log("startup brightness %s: no physical monitor" % dev)
                continue
            try:
                for i in range(n):
                    got = self.read_ddc(arr[i])
                    if got:
                        self.maxes[(h, i)] = got[1]
                        pct = int(round(got[0] * 100 / got[1]))
                        self.ddc_ok[h] = True
                        log("startup brightness %s (%s): %d of %d = %d%%" % (dev, arr[i].desc, got[0], got[1], pct))
                        if first is None:
                            first = pct
                    else:
                        self.ddc_ok[h] = self.ddc_ok.get(h, False)
                        log("startup brightness %s (%s): no agreeing DDC/CI reply" % (dev, arr[i].desc))
            finally:
                dxva.DestroyPhysicalMonitors(n, arr)
        if not all(self.ddc_ok.values()) or not self.ddc_ok:
            self.need_wmi = True
            try:
                wv = wmi_read_brightness()
                log("startup brightness via WMI: %s" % wv)
                if first is None and wv is not None:
                    first = wv
            except Exception as e:
                log("WMI read failed: %s" % e)
        return first

    def write_all(self, value):
        for h in monitors():
            arr, n = physical(h)
            if not arr:
                continue
            try:
                for i in range(n):
                    mx = self.maxes.get((h, i))
                    if not mx:
                        got = self.read_ddc(arr[i])
                        mx = got[1] if got else 100
                        self.maxes[(h, i)] = mx
                    raw = int(round(value * mx / 100.0))
                    for _ in range(3):
                        if dxva.SetVCPFeature(arr[i].h, 0x10, raw):
                            break
                        time.sleep(0.05)
                    else:
                        log("SetVCPFeature failed on %s" % monitor_device(h))
            finally:
                dxva.DestroyPhysicalMonitors(n, arr)
        if self.need_wmi:
            try:
                wmi_set_brightness(value)
            except Exception as e:
                log("WMI set failed: %s" % e)

    def run(self):
        ole32.CoInitializeEx(None, 0)
        try:
            self.results.put(("bright", self.startup_read()))
        except Exception:
            log_exc("startup brightness read")
            self.results.put(("bright", None))
        while True:
            with self.cond:
                while self.pending is None:
                    self.cond.wait()
                value, self.pending = self.pending, None
            try:
                self.write_all(value)
            except Exception:
                log_exc("brightness write")


# --------------------------------------------------------------------------- icons drawn on the Tk canvas
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


# --------------------------------------------------------------------------- tray icon picture
def _seg_dist(px_, py_, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    t = max(0.0, min(1.0, ((px_ - x0) * dx + (py_ - y0) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px_ - (x0 + t * dx), py_ - (y0 + t * dy))


def _hex(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def render_icon(size):
    """RGBA bytes (straight alpha), size x size, drawn on a 16-unit grid with 4x4 supersampling."""
    ss, k = 4, 16.0 / size
    knobs = [(4.5, 3, _hex(COL_VOL)), (11.5, 8, _hex(COL_BRIGHT)), (7.3, 13, _hex(COL_BLUE))]
    out = bytearray(size * size * 4)
    for py in range(size):
        for pxl in range(size):
            ar = ag = ab = aa = 0
            for sy in range(ss):
                for sx in range(ss):
                    ux, uy = (pxl + (sx + 0.5) / ss) * k, (py + (sy + 0.5) / ss) * k
                    col = None
                    for ty in (3, 8, 13):
                        if _seg_dist(ux, uy, 0.5, ty, 15.5, ty) <= 1.0:
                            col = (255, 255, 255)
                    for kx, ky, kc in knobs:
                        d = math.hypot(ux - kx, uy - ky)
                        if d <= 3.3:
                            col = (255, 255, 255)
                        if d <= 2.2:
                            col = kc
                    if col:
                        ar += col[0]
                        ag += col[1]
                        ab += col[2]
                        aa += 1
            o = (py * size + pxl) * 4
            if aa:
                out[o:o + 4] = bytes((round(ar / aa), round(ag / aa), round(ab / aa), round(255 * aa / (ss * ss))))
    return bytes(out)


def png_rgba(wd, ht, rgba):
    raw = b"".join(b"\x00" + rgba[y * wd * 4:(y + 1) * wd * 4] for y in range(ht))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", wd, ht, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_hicon(size, rgba):
    bmi = BMIH()
    bmi.biSize, bmi.biWidth, bmi.biHeight = ctypes.sizeof(BMIH), size, -size
    bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
    bits = ctypes.c_void_p()
    dc = user.GetDC(None)
    color = gdi.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    user.ReleaseDC(None, dc)
    bgra = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        bgra[i], bgra[i + 1], bgra[i + 2], bgra[i + 3] = rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]
    ctypes.memmove(bits, bytes(bgra), len(bgra))
    mask = gdi.CreateBitmap(size, size, 1, 1, None)
    ii = ICONINFO(True, 0, 0, mask, color)
    hicon = user.CreateIconIndirect(ctypes.byref(ii))
    gdi.DeleteObject(color)
    gdi.DeleteObject(mask)
    return hicon


# --------------------------------------------------------------------------- settings
def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def migrate_settings(st):
    """Convert older settings files. Safe to call repeatedly. True if it changed anything."""
    changed = False
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    bf = st.get("blue_floor")
    if not (number(bf) and 0 <= bf <= 1):
        bf = None
    if "floor" not in st and bf is not None:               # blue_floor is a fraction, floor a percent
        st["floor"] = int(round(bf * 100))
        changed = True
    blue = st.get("blue")
    if not number(blue):
        return changed
    if (isinstance(blue, float) or "blue_floor" in st) and 0 <= blue <= 1:
        # Layout from an earlier prompt: "blue" is the knob's position, 0.0 to 1.0.
        # This also repairs a version 4 file, once, because version 4 misread it.
        if st.get("blue_migrated"):
            return changed
        if bf is None:
            if not number(st.get("floor")):
                return changed                             # main() calls again after finding the floor
            bf = st["floor"] / 100.0
        st["blue_removed"] = int(round(blue * (1 - bf) * 100))
        st["blue_migrated"] = True
        return True
    if "blue_removed" not in st:                           # older layout: "blue" is percent of blue kept
        st["blue_removed"] = max(0, 100 - int(blue))
        changed = True
    return changed


def real_path(p):
    try:
        return os.path.normcase(os.path.realpath(p))
    except (OSError, ValueError):
        return os.path.normcase(p)


def known_folder(guid_text):
    """The real path behind a {known-folder GUID}, or None."""
    try:
        g = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid_text).bytes_le)
        out = ctypes.c_wchar_p()
        shell = ctypes.WinDLL("shell32")
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        if shell.SHGetKnownFolderPath(g, 0, None, ctypes.byref(out)) != 0:
            return None
        path = out.value
        ctypes.WinDLL("ole32").CoTaskMemFree(out)
        return path
    except (OSError, ValueError):
        return None


def same_exe(stored):
    """Is this notification entry ours? Windows stores the resolved path, which differs from
    sys.executable behind a junction, symlink, subst drive or short name, and it may use a
    {known-folder GUID}\\... prefix."""
    s = str(stored)
    plain = os.path.normcase(sys.executable)
    mine = (plain, real_path(sys.executable))
    if s.startswith("{") and "}" in s:
        guid, _, rest = s.partition("}")
        rest = rest.lstrip("\\")
        if not rest:
            return False
        base = known_folder(guid + "}")
        if base:
            whole = os.path.join(base, rest)
            if real_path(whole) in mine or os.path.normcase(whole) in mine:
                return True
        tail = os.path.normcase("\\" + rest)
        return any(m.endswith(tail) for m in mine)
    return real_path(s) in mine or os.path.normcase(s) in mine


# window-procedure side: plain Python lists only, never Tcl
WHEEL = []
OLD_PROCS = {}
_proc_refs = []


def _subclass(hwnd):
    def proc(h, msg, wp, lp):
        try:
            if msg == 0x21:                       # WM_MOUSEACTIVATE
                return 3                          # MA_NOACTIVATE
            if msg == 0x20A:                      # WM_MOUSEWHEEL
                WHEEL.append((ctypes.c_short((wp >> 16) & 0xFFFF).value,
                              ctypes.c_short(lp & 0xFFFF).value,
                              ctypes.c_short((lp >> 16) & 0xFFFF).value))
                return 0
        except Exception:
            pass
        return user.CallWindowProcW(OLD_PROCS[h], h, msg, wp, lp)

    cb = WNDPROC(proc)
    _proc_refs.append(cb)
    OLD_PROCS[hwnd] = user.SetWindowLongPtrW(hwnd, -4, ctypes.cast(cb, ctypes.c_void_p).value)


# --------------------------------------------------------------------------- automatic updates
UPDATE_URL = "https://bennylabs.org/my-prompts/sliders/latest.json"
UPDATE_STATE = os.path.join(HERE, "update_state.json")
CHECK_EVERY = 12 * 3600
UPDATE_READY = []             # filled by the updater thread, emptied by the Tk timer
MUTEX = None
DETACHED = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

# Runs by itself after an update. If the new version has not confirmed it is healthy
# after 90 seconds, this puts the old one back and never tries that version again.
WATCHDOG = r"""
import json, os, shutil, subprocess, sys, time
script, backup, path, version = sys.argv[1:5]
version = int(version)
time.sleep(90)
try:
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
except Exception:
    st = {}
p = st.get("pending")
if p and p.get("version") == version:
    if p.get("pid"):
        try:
            os.kill(p["pid"], 9)
        except Exception:
            pass
    time.sleep(2)
    shutil.copy2(backup, script)
    st["pending"] = None
    st["bad"] = st.get("bad", []) + [version]
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(path + ".tmp", path)
    with open(os.path.join(os.path.dirname(script), "screen_sliders.log"), "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " version %d did not start properly; went back to the old copy\n" % version)
    subprocess.Popen([sys.executable, script], creationflags=0x00000208, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"""


def load_update_state():
    try:
        with open(UPDATE_STATE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_update_state(st):
    tmp = UPDATE_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, UPDATE_STATE)


def update_url_allowed(url):
    return isinstance(url, str) and (url.startswith("https://") or url.startswith("http://127.0.0.1"))


def is_due(last, now):
    return not isinstance(last, (int, float)) or now - last >= CHECK_EVERY or now < last


def fetch(url, limit=2 * 1024 * 1024):
    if not update_url_allowed(url):
        raise ValueError("refused address")
    req = urllib.request.Request(url, headers={"User-Agent": "ScreenSliders/" + VERSION, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("too large")
    return data


def pick_update(manifest, get, current, bad):
    """Returns (version, program bytes) when a good newer version exists, else None."""
    f = manifest["files"]["windows"]
    v = f["version"]
    if not isinstance(v, int) or isinstance(v, bool) or v <= current or v in bad:
        return None
    data = get(f["url"])
    if hashlib.sha256(data).hexdigest().lower() != str(f["sha256"]).lower():
        raise ValueError("version %d does not match its fingerprint" % v)
    text = data.decode("utf-8")
    if ('VERSION = "%d"' % v) not in text:
        raise ValueError("version %d has the wrong version line" % v)
    compile(text, "screen_sliders", "exec")
    return v, data


class Updater(threading.Thread):
    """Never touches Tk. A failed check is ignored and tried again at the next one."""
    def __init__(self, settings):
        threading.Thread.__init__(self, daemon=True)
        self.settings = settings

    def run(self):
        time.sleep(self.settings.get("update_first_check", 120))
        while True:
            try:
                if self.settings.get("auto_update", True) is not False and self.check():
                    return
            except Exception:
                log_exc("update check")
            time.sleep(600)            # wakes often, so a computer that slept still checks on time

    def check(self):
        state = load_update_state()
        now = time.time()
        if not is_due(state.get("last_check"), now):
            return False
        state["last_check"] = now
        save_update_state(state)
        url = self.settings.get("update_url", UPDATE_URL)
        try:
            manifest = json.loads(fetch(url).decode("utf-8"))
        except (OSError, ValueError):
            return False               # offline, or the site is down
        found = pick_update(manifest, fetch, int(VERSION), state.get("bad", []))
        if found:
            UPDATE_READY.append(found)
            return True
        return False


# --------------------------------------------------------------------------- the bar
class Bar:
    def __init__(self, st, prev_fg):
        self.st = st
        # A different Python gets a new, switched-off entry in Taskbar settings, so switch that one on once.
        me = real_path(sys.executable)
        if st.get("tray_exe") and real_path(st["tray_exe"]) != me:
            st["tray_switch_set"] = False
        st["tray_exe"] = me
        self.no_entry_logged = False
        self.started = time.time()
        self.confirmed = False
        self.results = queue.Queue()
        self.boost_until = 0.0
        self.hook_pid = None
        self.hooks = []
        self.visible = False
        self.n = 0
        self.active = None
        self.wheel_acc = {"vol": 0.0, "bright": 0.0, "blue": 0.0}
        self.last_tray_hwnd = None
        self.entry = None
        self.entry_checked = 0.0
        self.icon_png = None
        self.hicon = None
        self.bright_touched = False
        self.bg, self.fg, self.track = "#202020", "#f2f2f2", "#6a6a6a"

        tb = user.FindWindowW("Shell_TrayWnd", None)
        self.S = (user.GetDpiForWindow(tb) or 96) / 96.0 if tb else 1.0
        S = self.S
        self.W, self.H = self.px(385), self.px(40)
        self.zones = {"vol": (self.px(14), self.px(113)), "bright": (self.px(143), self.px(242)),
                      "blue": (self.px(272), self.px(371))}
        self.tip_on = False
        self.tip_text = ""
        self.tip_pos = None
        self.tip_hold = None
        self.font = ("Segoe UI", -self.px(12))

        self.floor = int(self.st["floor"])                       # percent of blue kept at the far right
        self.maxrem = max(1, 100 - self.floor)
        self.blue = max(0, min(self.maxrem, int(self.st.get("blue_removed", 0))))
        self.bright = int(self.st.get("bright", 50))
        rv = read_volume()
        self.volume, self.muted = rv if rv else (0, False)

        self.root = tk.Tk()
        self.root.title("ScreenSliders")
        self.root.report_callback_exception = lambda t, v, tb_: log("tk callback:\n" + "".join(traceback.format_exception(t, v, tb_)))
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.configure(bg=self.bg)
        self.cv = tk.Canvas(self.root, width=self.W, height=self.H, highlightthickness=0, bd=0, bg=self.bg)
        self.cv.pack()
        self.root.geometry("%dx%d+0+0" % (self.W, self.H))
        self.root.update_idletasks()
        self.hwnd = user.GetAncestor(self.root.winfo_id(), 2)
        self.cv_hwnd = self.cv.winfo_id()
        self.fix_styles()
        _subclass(self.hwnd)
        if self.cv_hwnd != self.hwnd:
            _subclass(self.cv_hwnd)
        x, y = self.target_pos()
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, x, y))
        self.root.deiconify()
        self.root.update()
        self.fix_styles()
        self.visible = True
        user.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self.W, self.H, SWP_NOACTIVATE)
        self.make_tip()
        # Tk may have activated the window when it mapped it: give the focus back.
        fg = user.GetForegroundWindow()
        if prev_fg and fg != prev_fg and (fg in (self.hwnd, self.cv_hwnd, self.tip_hwnd) or user.GetAncestor(fg, 2) in (self.hwnd, self.tip_hwnd)):
            user.SetForegroundWindow(prev_fg)

        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)

        self.worker = BrightnessWorker(self.results)
        self.worker.start()
        self.setup_tray()
        self.sample_colour()
        self.draw()
        self.root.after(40, self.tick)

    # -- helpers
    def px(self, v):
        return int(round(v * self.S))

    def fix_styles(self):
        ex = user.GetWindowLongPtrW(self.hwnd, -20)
        ex = (ex | 0x80 | 0x08000000) & ~0x40000     # TOOLWINDOW | NOACTIVATE, not APPWINDOW
        user.SetWindowLongPtrW(self.hwnd, -20, ex)

    def make_tip(self):
        """A small label above the taskbar showing the percentage. It never takes the focus."""
        tip = self.tip = tk.Toplevel(self.root)
        tip.title("ScreenSlidersTip")
        tip.overrideredirect(True)
        tip.configure(bg="#2b2b2b")
        self.tip_label = tk.Label(tip, text="100%", font=self.font, bg="#2b2b2b", fg="#ffffff",
                                  padx=self.px(7), pady=self.px(3), bd=0)
        self.tip_label.pack()
        tip.geometry("+-4000+-4000")
        tip.update()
        self.tip_w, self.tip_h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        self.tip_hwnd = user.GetAncestor(tip.winfo_id(), 2)
        ex = user.GetWindowLongPtrW(self.tip_hwnd, -20)
        user.SetWindowLongPtrW(self.tip_hwnd, -20, (ex | 0x80 | 0x08000000) & ~0x40000)
        _subclass(self.tip_hwnd)
        user.ShowWindow(self.tip_hwnd, 0)

    def tip_target(self):
        """Which slider's tooltip should show: the one being dragged or wheeled, or the knob under the mouse."""
        if not self.visible:
            return None
        if self.active:
            return self.active
        if self.tip_hold and time.time() < self.tip_hold[1]:
            return self.tip_hold[0]
        pt, rc = POINT(), RECT()
        user.GetCursorPos(ctypes.byref(pt))
        user.GetWindowRect(self.hwnd, ctypes.byref(rc))
        if not (rc.l <= pt.x < rc.r and rc.t <= pt.y < rc.b):
            return None
        for name in self.zones:
            if abs(pt.x - rc.l - self.slider_x(name)) <= self.px(12):
                return name
        return None

    def update_tip(self):
        name = self.tip_target()
        if not name:
            if self.tip_on:
                user.ShowWindow(self.tip_hwnd, 0)
                self.tip_on = False
            return
        value = {"vol": self.volume, "bright": self.bright, "blue": self.blue}[name]
        text = "%d%%" % value
        if text != self.tip_text:
            self.tip_label.configure(text=text)
            self.tip_text = text
        g = self.taskbar_geometry()
        if not g:
            return
        rc = RECT()
        user.GetWindowRect(self.hwnd, ctypes.byref(rc))
        pos = (rc.l + int(self.slider_x(name)) - self.tip_w // 2, g[1].t - self.tip_h - self.px(6))
        if pos != self.tip_pos or not self.tip_on:
            user.SetWindowPos(self.tip_hwnd, HWND_TOPMOST, pos[0], pos[1], self.tip_w, self.tip_h, SWP_NOACTIVATE)
            self.tip_pos = pos
        if not self.tip_on:
            user.ShowWindow(self.tip_hwnd, 4)          # SW_SHOWNOACTIVATE
            self.tip_on = True

    def apply_update(self, version, data):
        """Swap in the new version and restart. Anything failing before the restart leaves the old one running."""
        script = os.path.abspath(__file__)
        backup = os.path.join(HERE, "screen_sliders_v%s.pyw" % VERSION)
        n = 2
        while os.path.exists(backup):
            backup = os.path.join(HERE, "screen_sliders_v%s-%d.pyw" % (VERSION, n))
            n += 1
        new = script + ".new"
        with open(script, "rb") as f:
            old = f.read()
        with open(backup, "wb") as f:
            f.write(old)
        with open(new, "wb") as f:
            f.write(data)
        state = load_update_state()
        state["pending"] = {"version": version, "from": int(VERSION), "backup": backup, "pid": None}
        save_update_state(state)
        os.replace(new, script)
        quiet = dict(creationflags=DETACHED, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            subprocess.Popen([sys.executable, "-I", "-c", WATCHDOG, script, backup, UPDATE_STATE, str(version)], **quiet)
            self.save()
            kernel.CloseHandle(MUTEX)
            subprocess.Popen([sys.executable, script], **quiet)
        except Exception:
            with open(script, "wb") as f:
                f.write(old)
            state["pending"] = None
            save_update_state(state)
            raise
        os._exit(0)

    def update_tick(self):
        if UPDATE_READY and not self.active:
            version, data = UPDATE_READY.pop()
            self.apply_update(version, data)

    def confirm_tick(self):
        """After an update, tell the watchdog this version really works: running, with the bar showing."""
        if self.confirmed or time.time() - self.started < 30:
            return
        state = load_update_state()
        p = state.get("pending")
        if not p or p.get("version") != int(VERSION):
            self.confirmed = True
            return
        g = self.taskbar_geometry()
        if g and self.switch_on() and not self.fullscreen_in_front(g[0], g[1]) and not user.IsWindowVisible(self.hwnd):
            return
        state["pending"] = None
        save_update_state(state)
        self.confirmed = True

    def save(self):
        self.st["bright"] = self.bright
        self.st["blue_removed"] = self.blue
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(self.st, f)
        except OSError:
            log_exc("save settings")

    def taskbar_geometry(self):
        tb = user.FindWindowW("Shell_TrayWnd", None)
        if not tb:
            return None
        rc = RECT()
        user.GetWindowRect(tb, ctypes.byref(rc))
        tray = user.FindWindowExW(tb, None, "TrayNotifyWnd", None)
        right = rc.r
        if tray:
            tr = RECT()
            user.GetWindowRect(tray, ctypes.byref(tr))
            right = tr.l
        return tb, rc, right

    def target_pos(self):
        g = self.taskbar_geometry()
        if not g:
            return 0, 0
        tb, rc, right = g
        return right - self.W - self.px(10), rc.t + ((rc.b - rc.t) - self.H) // 2

    # -- drawing
    def slider_x(self, name):
        x0, x1 = self.zones[name]
        return x0 + (x1 - x0) * self.frac(name)

    def frac(self, name):
        if name == "vol":
            return self.volume / 100.0
        if name == "bright":
            return self.bright / 100.0
        return self.blue / float(self.maxrem)

    def draw(self):
        cv, mid, s = self.cv, self.H // 2, self.S
        cv.delete("all")
        spec = (("vol", draw_speaker, COL_VOL, self.volume), ("bright", draw_sun, COL_BRIGHT, self.bright),
                ("blue", draw_bulb, COL_BLUE, self.blue))
        for name, icon, color, value in spec:
            x0, x1 = self.zones[name]
            vx = self.slider_x(name)
            cv.create_line(x0, mid, x1, mid, width=self.px(4), capstyle="round", fill=self.track)
            cv.create_line(x0, mid, vx, mid, width=self.px(4), capstyle="round", fill=color)
            r = s * 12
            cv.create_oval(vx - r, mid - r, vx + r, mid + r, fill=self.bg, outline=self.bg)
            if name == "vol":
                icon(cv, vx, mid, color, self.frac(name), s, muted=self.muted or self.volume == 0)
            else:
                icon(cv, vx, mid, color, self.frac(name), s)

    # -- input
    def hit(self, x):
        for name, (x0, x1) in self.zones.items():
            if x0 - self.px(10) <= x <= x1 + self.px(10):
                return name
        return None

    def block_at(self, x):
        """Which slider (track and knob) the pointer is over, for the wheel."""
        if x < self.px(128):
            return "vol"
        if x < self.px(257):
            return "bright"
        return "blue"

    def set_from_x(self, name, x):
        x0, x1 = self.zones[name]
        f = max(0.0, min(1.0, (x - x0) / float(x1 - x0)))
        self.apply(name, f)

    def apply(self, name, f):
        """f = 0..1 position along the slider."""
        if name == "vol":
            new = int(round(f * 100))
            if new != self.volume:
                self.volume = new
                write_volume(new)
                if new > 0:
                    self.muted = False
        elif name == "bright":
            new = int(round(f * 100))
            if new != self.bright:
                self.bright = new
                self.bright_touched = True
                self.worker.push(new)
        else:
            new = int(round(f * self.maxrem))
            if new != self.blue:
                self.blue = new
                set_blue(new)
        self.draw()

    def on_press(self, e):
        self.active = self.hit(e.x)
        if self.active:
            self.set_from_x(self.active, e.x)
            self.safe(self.update_tip)

    def on_drag(self, e):
        if self.active:
            self.set_from_x(self.active, e.x)
            self.safe(self.update_tip)

    def on_release(self, _e):
        self.active = None
        self.save()

    def process_wheel(self):
        while WHEEL:
            delta, sx, sy = WHEEL.pop(0)
            pt = POINT(sx, sy)
            user.ScreenToClient(self.hwnd, ctypes.byref(pt))
            name = self.block_at(pt.x)
            self.wheel_acc[name] += delta / 120.0 * 2
            step = int(self.wheel_acc[name])        # whole points only; wheel up = knob to the right
            if step:
                self.wheel_acc[name] -= step
                cur = {"vol": self.volume, "bright": self.bright, "blue": self.blue}[name]
                top = {"vol": 100, "bright": 100, "blue": self.maxrem}[name]
                new = max(0, min(top, cur + step))
                self.apply(name, new / float(top))
                self.save()
                self.tip_hold = (name, time.time() + 1.2)

    # -- timers
    def safe(self, fn):
        try:
            fn()
        except Exception:
            log_exc(fn.__name__)

    def tick(self):
        self.n += 1
        self.safe(self.process_wheel)
        self.safe(self.update_tip)
        self.safe(self.update_tick)
        if self.visible and time.time() < self.boost_until:
            user.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        if self.n % 5 == 0:
            self.safe(self.drain_results)
        if self.n % 50 == 0:
            self.safe(self.confirm_tick)
        if self.n % 10 == 0:
            self.safe(self.layout_tick)
        if self.n % 20 == 0:
            self.safe(self.poll_volume)
        if self.n % 125 == 0:
            self.safe(self.sample_colour)
            self.safe(self.reapply_blue)
            self.safe(self.sync_icon_snapshot)
        self.root.after(40, self.tick)

    def drain_results(self):
        while True:
            try:
                kind, val = self.results.get_nowait()
            except queue.Empty:
                return
            if kind == "bright" and val is not None and not self.bright_touched:
                self.bright = int(val)
                self.draw()

    def poll_volume(self):
        if self.active == "vol":
            return
        now = read_volume()
        if now and now != (self.volume, self.muted):
            self.volume, self.muted = now
            self.draw()

    def reapply_blue(self):
        if self.blue > 0 and not ramp_matches(self.blue):
            set_blue(self.blue)

    def sample_colour(self):
        g = self.taskbar_geometry()
        if not g or not self.visible and self.n:
            return
        tb, rc, right = g
        x, y = self.target_pos()
        dc = user.GetDC(None)
        c = gdi.GetPixel(dc, x - 6, rc.t + self.px(4))
        user.ReleaseDC(None, dc)
        if c == 0xFFFFFFFF:
            return
        r, g_, b = c & 255, (c >> 8) & 255, (c >> 16) & 255
        new = "#%02x%02x%02x" % (r, g_, b)
        if new != self.bg:
            self.bg = new
            light = (r * 299 + g_ * 587 + b * 114) / 1000 > 128
            self.fg = "#222222" if light else "#f2f2f2"
            self.track = "#b0b0b0" if light else "#6a6a6a"
            self.cv.configure(bg=new)
            self.root.configure(bg=new)
            self.draw()

    # -- visibility, position, hooks
    def fullscreen_in_front(self, tb, rc):
        fg = user.GetForegroundWindow()
        if not fg or fg == self.hwnd or fg == tb:
            return False
        name = ctypes.create_unicode_buffer(64)
        user.GetClassNameW(fg, name, 64)
        if name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False
        r = RECT()
        user.GetWindowRect(fg, ctypes.byref(r))
        mi = MI()
        mi.cb = ctypes.sizeof(MI)
        user.GetMonitorInfoW(user.MonitorFromWindow(tb, 2), ctypes.byref(mi))
        return r.l <= rc.l and r.r >= rc.r and r.t <= mi.rc.t and r.b >= rc.b

    def layout_tick(self):
        g = self.taskbar_geometry()
        if not g:
            return
        tb, rc, right = g
        if tb != self.last_tray_hwnd:
            self.last_tray_hwnd = tb
            self.setup_tray()
            self.setup_hooks(tb)
        want = (not self.fullscreen_in_front(tb, rc)) and self.switch_on()
        x = right - self.W - self.px(10)
        y = rc.t + ((rc.b - rc.t) - self.H) // 2
        if want:
            if not self.visible:
                user.ShowWindow(self.hwnd, 4)         # SW_SHOWNOACTIVATE
                self.visible = True
            user.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self.W, self.H, SWP_NOACTIVATE)
        elif self.visible:
            user.ShowWindow(self.hwnd, 0)
            self.visible = False

    def setup_hooks(self, tb):
        pid = w.DWORD()
        user.GetWindowThreadProcessId(tb, ctypes.byref(pid))
        if pid.value == self.hook_pid:
            return
        for hk in self.hooks:
            user.UnhookWinEvent(hk)
        self.hooks = []
        self.hook_pid = pid.value

        def cb(_hook, _event, _hwnd, _obj, _child, _thread, _time):
            try:
                if self.visible:
                    user.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                self.boost_until = time.time() + 0.6
            except Exception:
                pass

        self.hook_cb = WINEVENTPROC(cb)
        for ev in (0x0003, 0x8004):                   # FOREGROUND, OBJECT_REORDER
            hk = user.SetWinEventHook(ev, ev, None, self.hook_cb, pid.value, 0, 0x2)   # OUTOFCONTEXT|SKIPOWNPROCESS
            if hk:
                self.hooks.append(hk)
            else:
                log("SetWinEventHook failed for event %x" % ev)

    # -- tray switch
    def setup_tray(self):
        size = user.GetSystemMetrics(49) or 16
        if self.hicon is None:
            self.hicon = make_hicon(size, render_icon(size))
        nid = NID()
        nid.cbSize = ctypes.sizeof(NID)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = 0x2 | 0x4 | 0x8                  # NIF_ICON | NIF_TIP | NIF_STATE
        nid.hIcon = self.hicon
        nid.szTip = TRAY_TIP
        nid.dwState = nid.dwStateMask = 0x1           # NIS_HIDDEN
        if not shell.Shell_NotifyIconW(0, ctypes.byref(nid)):           # NIM_ADD
            shell.Shell_NotifyIconW(1, ctypes.byref(nid))               # already there: NIM_MODIFY

    def find_entry(self):
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_KEY)
        except OSError:
            return None
        with root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    return None
                i += 1
                try:
                    with winreg.OpenKey(root, name) as k:
                        tip = winreg.QueryValueEx(k, "InitialTooltip")[0]
                        exe = winreg.QueryValueEx(k, "ExecutablePath")[0]
                except OSError:
                    continue
                if tip == TRAY_TIP and same_exe(exe):
                    return name

    def entry_key(self, access):
        if self.entry is None and time.time() - self.entry_checked > 1.5:
            self.entry_checked = time.time()
            self.entry = self.find_entry()
        if self.entry is None:
            return None
        try:
            return winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_KEY + "\\" + self.entry, 0, access)
        except OSError:
            self.entry = None
            return None

    def switch_on(self):
        k = self.entry_key(winreg.KEY_READ | winreg.KEY_SET_VALUE)
        if k is None:
            # A missing entry must never hide the bar. Hide only when the entry says so.
            if self.st.get("tray_switch_set") and not self.no_entry_logged:
                self.no_entry_logged = True
                log("no Taskbar settings entry matches " + sys.executable + "; keeping the bar visible")
            return True
        with k:
            if not self.st.get("tray_switch_set"):
                winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 1)
                self.st["tray_switch_set"] = True
                self.save()
                return True
            try:
                return winreg.QueryValueEx(k, "IsPromoted")[0] == 1
            except OSError:
                return False

    def sync_icon_snapshot(self):
        if self.icon_png is None:
            self.icon_png = png_rgba(16, 16, render_icon(16))
        k = self.entry_key(winreg.KEY_READ | winreg.KEY_SET_VALUE)
        if k is None:
            return
        with k:
            try:
                cur = winreg.QueryValueEx(k, "IconSnapshot")[0]
            except OSError:
                cur = None
            if cur != self.icon_png:
                winreg.SetValueEx(k, "IconSnapshot", 0, winreg.REG_BINARY, self.icon_png)


def main():
    global MUTEX
    MUTEX = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:               # another copy is running
        return
    state = load_update_state()
    p = state.get("pending")
    if p and p.get("version") == int(VERSION):       # just updated: let the watchdog know who to check
        p["pid"] = os.getpid()
        save_update_state(state)
    set_dpi_aware()
    prev_fg = user.GetForegroundWindow()
    ole32.CoInitializeEx(None, 2)                    # apartment threaded, for Tk's thread
    st = load_settings()
    changed = migrate_settings(st)
    if "floor" not in st:
        st["floor"] = find_blue_floor()
        migrate_settings(st)
        changed = True
    if changed:
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(st, f)
        except OSError:
            pass
    bar = Bar(st, prev_fg)
    if bar.blue > 0:
        set_blue(bar.blue)
    Updater(st).start()
    bar.root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        log_exc("fatal")
