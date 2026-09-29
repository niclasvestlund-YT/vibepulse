"""Mac Keychain, with OpenPulse-only service/account names and no argv secrets.

VibePulse's Keychain storage pattern is reused, using Security.framework
instead of `security -w SECRET` so writes also avoid process arguments.
"""
import ctypes
import os
import re
import sys

SERVICE = b"org.openpulse.openrouter"


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,23}", value) is not None


def keychain(account, secret=None, *, remove=False):
    if sys.platform != "darwin" or not valid_id(account):
        raise RuntimeError("mac_keychain_unavailable")
    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    find = security.SecKeychainFindGenericPassword
    find.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
                     ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
                     ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    find.restype = ctypes.c_int32
    security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
    security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    security.SecKeychainItemModifyAttributesAndData.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                              ctypes.c_uint32, ctypes.c_char_p]
    security.SecKeychainAddGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
        ctypes.c_char_p, ctypes.c_void_p]
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    account_bytes = account.encode()
    length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
    status = find(None, len(SERVICE), SERVICE, len(account_bytes), account_bytes,
                  ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
    try:
        if remove:
            if status == -25300:
                return None
            if status or security.SecKeychainItemDelete(item):
                raise RuntimeError("keychain_delete_failed")
            return None
        if secret is None:
            if status == -25300:
                return None
            if status:
                raise RuntimeError("keychain_locked_or_denied")
            return ctypes.string_at(data, length.value).decode()
        encoded = secret.encode()
        if status == -25300:
            status = security.SecKeychainAddGenericPassword(None, len(SERVICE), SERVICE,
                len(account_bytes), account_bytes, len(encoded), encoded, None)
        elif status == 0:
            status = security.SecKeychainItemModifyAttributesAndData(item, None, len(encoded), encoded)
        if status:
            raise RuntimeError("keychain_write_failed")
        return None
    finally:
        if data:
            security.SecKeychainItemFreeContent(None, data)
        if item:
            cf.CFRelease(item)


def load(account):
    if not valid_id(account):
        raise RuntimeError("invalid_credential_id")
    return os.environ.get("OPENPULSE_KEY_" + account.upper()) or keychain(account)
