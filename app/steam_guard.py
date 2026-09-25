import base64
import binascii
import hashlib
import hmac
import json
import struct
import time

STEAM_CODE_ALPHABET = "23456789BCDFGHJKMNPQRTVWXY"
STEAM_CODE_PERIOD = 30
FRESH_CODE_MIN_LIFETIME = 26


def parse_mafile(content: bytes) -> dict[str, str]:
    if len(content) > 256_000:
        raise ValueError("Файл завеликий.")
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ValueError("Це невалідний .maFile (очікується JSON).") from None
    if not isinstance(data, dict):
        raise ValueError("Це невалідний .maFile.")
    shared_secret = data.get("shared_secret")
    account_name = data.get("account_name")
    session = data.get("Session") or data.get("session") or {}
    steam_id = session.get("SteamID") if isinstance(session, dict) else None
    if not isinstance(shared_secret, str) or not shared_secret:
        raise ValueError("У .maFile немає shared_secret.")
    try:
        decoded = base64.b64decode(shared_secret, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("shared_secret у .maFile пошкоджений.") from None
    if len(decoded) < 16:
        raise ValueError("shared_secret у .maFile пошкоджений.")
    if not isinstance(account_name, str) or not account_name.strip():
        raise ValueError("У .maFile немає account_name.")
    return {
        "account_name": account_name.strip(),
        "steam_id": str(steam_id or "").strip(),
        "shared_secret": shared_secret,
    }


def generate_steam_guard_code(shared_secret: str, timestamp: int | None = None) -> str:
    try:
        secret = base64.b64decode(shared_secret, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("invalid_shared_secret") from None
    counter = int(timestamp if timestamp is not None else time.time()) // STEAM_CODE_PERIOD
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code = []
    for _ in range(5):
        code.append(STEAM_CODE_ALPHABET[value % len(STEAM_CODE_ALPHABET)])
        value //= len(STEAM_CODE_ALPHABET)
    return "".join(code)


def fresh_code_wait_seconds(timestamp: float | None = None) -> int:
    """Seconds to wait until a code has at least 26 seconds of life remaining."""
    current = timestamp if timestamp is not None else time.time()
    age = current % STEAM_CODE_PERIOD
    if age <= STEAM_CODE_PERIOD - FRESH_CODE_MIN_LIFETIME:
        return 0
    return max(1, int(STEAM_CODE_PERIOD - age) + 1)
