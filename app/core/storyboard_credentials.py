"""Protect remote credentials with the current Windows account (DPAPI)."""
import base64
import ctypes
import os


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Credential storage requires Windows DPAPI. Use RUNPOD_API_KEY for this platform.")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    api = ctypes.windll.crypt32
    fn = api.CryptUnprotectData if decrypt else api.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("Cannot access the saved credential with this Windows account. Enter it again.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def protect(value):
    return base64.b64encode(_crypt(value.encode("utf-8"))).decode("ascii") if value else ""


def reveal(value):
    return _crypt(base64.b64decode(value), True).decode("utf-8") if value else ""
