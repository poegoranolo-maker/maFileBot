import base64
import re
from datetime import UTC, datetime
from email.utils import parseaddr
from html import unescape
from urllib.parse import urlencode

import httpx

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def body_text(payload):
    """Walk nested MIME; attachments and unrelated MIME types are ignored."""
    chunks = []
    if payload.get("mimeType") in ("text/plain", "text/html"):
        data = payload.get("body", {}).get("data")
        if data:
            raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
            chunks.append(unescape(re.sub(r"<[^>]+>", "\n", raw)))
    for part in payload.get("parts", []):
        chunks.append(body_text(part))
    return "\n".join(chunks)


def parse_steam_code(message, login, earliest, current=None, search_settings=None):
    current = current or datetime.now(UTC)
    timestamp = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, UTC)
    if timestamp < earliest or timestamp > current:
        return None
    payload = message.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    settings = _normalized_parser_settings(search_settings)
    if not _matches_headers(headers, settings):
        return None
    # Do not distribute account recovery, password reset, or email-change codes.
    subject = headers.get("subject", "").lower()
    if not _is_safe_subject(subject):
        return None
    text = body_text(payload)
    # Shared Steam mailboxes need an account-login match. Providers such as EA
    # can omit it, so administrators may explicitly disable this check.
    if settings["require_login"] and not re.search(
        r"(?<![\w])" + re.escape(login) + r"(?![\w])", text, re.IGNORECASE
    ):
        return None
    codes = _steam_codes(text, settings)
    return next(iter(codes)) if len(codes) == 1 else None


def _is_safe_subject(subject):
    blocked = (
        "password",
        "recovery",
        "recover",
        "email change",
        "change your email",
        "зміна пошти",
        "відновлення пароля",
        "смена почты",
        "восстановление пароля",
        "contraseña",
        "mot de passe",
        "passwort",
        "senha",
    )
    return not any(marker in subject for marker in blocked)


def _normalized_parser_settings(settings):
    defaults = {
        "code_length": 5,
        "allow_spaces": False,
        "require_login": True,
        "code_type": "alnum",
        "body_keyword": "Steam Guard",
        "sender": "noreply@steampowered.com",
        "subject": "",
    }
    if not isinstance(settings, dict):
        return defaults
    if isinstance(settings.get("code_length"), int):
        defaults["code_length"] = min(64, max(1, settings["code_length"]))
    if isinstance(settings.get("allow_spaces"), bool):
        defaults["allow_spaces"] = settings["allow_spaces"]
    if isinstance(settings.get("require_login"), bool):
        defaults["require_login"] = settings["require_login"]
    if settings.get("code_type") in {"alnum", "letters", "digits"}:
        defaults["code_type"] = settings["code_type"]
    for key in ("body_keyword", "sender", "subject"):
        if isinstance(settings.get(key), str):
            defaults[key] = settings[key].strip()
    return defaults


def _matches_headers(headers, settings):
    sender = settings["sender"].strip().lower()
    if sender and parseaddr(headers.get("from", ""))[1].lower() != sender:
        return False
    subject = headers.get("subject", "")
    return not settings["subject"] or settings["subject"].casefold() in subject.casefold()


def _steam_codes(text, settings=None):
    settings = _normalized_parser_settings(settings)
    character_class = {
        "alnum": "A-Z0-9",
        "letters": "A-Z",
        "digits": "0-9",
    }.get(settings["code_type"], "A-Z0-9")
    length = settings["code_length"] if isinstance(settings["code_length"], int) else 5
    length = min(64, max(1, length))
    separator = r"[ \t]*" if settings["allow_spaces"] else ""
    code_pattern = f"([{character_class}](?:{separator}[{character_class}]){{{length - 1}}})"
    keyword = settings["body_keyword"].strip()
    keyword_pattern = (
        r"steam[\s\-\u2010-\u2015]*guard"
        if keyword.casefold() == "steam guard"
        else re.escape(keyword)
    )
    prefix = rf"(?i:{keyword_pattern})(?s:.{{0,500}}?)" if keyword else ""
    return set(
        re.sub(r"\s+", "", code)
        for code in re.findall(rf"{prefix}(?<![A-Za-z0-9]){code_pattern}(?![A-Za-z0-9])", text)
    )


def parse_latest_steam_code(message, accounts, earliest, current=None, search_settings=None):
    current = current or datetime.now(UTC)
    timestamp = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, UTC)
    if timestamp < earliest or timestamp > current:
        return None
    payload = message.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    settings = _normalized_parser_settings(search_settings)
    if not _matches_headers(headers, settings):
        return None
    subject = headers.get("subject", "").lower()
    if not _is_safe_subject(subject):
        return None
    text = body_text(payload)
    codes = _steam_codes(text, settings)
    if len(codes) != 1:
        return None
    searchable = subject + "\n" + text
    matches = [
        (login, game)
        for login, game in accounts
        if re.search(r"(?<![\w])" + re.escape(login) + r"(?![\w])", searchable, re.IGNORECASE)
    ]
    login, game = matches[0] if len(matches) == 1 else ("—", "Гру не вдалося визначити")
    return next(iter(codes)), login, game, timestamp


class Gmail:
    def __init__(self, cfg, client: httpx.AsyncClient):
        self.cfg = cfg
        self.client = client

    @property
    def redirect(self):
        return self.cfg.public_base_url + "/oauth/gmail/callback"

    def authorize_url(self, state):
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.cfg.google_client_id,
                "redirect_uri": self.redirect,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )

    async def token(self, **grant):
        response = await self.client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.cfg.google_client_id,
                "client_secret": self.cfg.google_client_secret.get_secret_value(),
                **grant,
            },
        )
        response.raise_for_status()
        return response.json()

    async def exchange(self, code):
        tokens = await self.token(code=code, redirect_uri=self.redirect, grant_type="authorization_code")
        if not tokens.get("refresh_token"):
            raise ValueError("missing_refresh_token")
        profile = await self.get("profile", tokens["access_token"])
        return {"email": profile["emailAddress"], "refresh_token": tokens["refresh_token"]}

    async def get(self, path, token, **params):
        response = await self.client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
            headers={"Authorization": "Bearer " + token},
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def latest_code(self, credentials, login, earliest, search_settings=None):
        token = (await self.token(grant_type="refresh_token", refresh_token=credentials["refresh_token"]))[
            "access_token"
        ]
        settings = _normalized_parser_settings(search_settings)
        query = f"after:{int(earliest.timestamp())}"
        if settings["sender"]:
            query = f"from:{settings['sender']} " + query
        result = await self.get(
            "messages",
            token,
            q=query,
            maxResults=20,
        )
        messages = []
        for item in result.get("messages", []):
            messages.append(await self.get("messages/" + item["id"], token, format="full"))
        for message in sorted(messages, key=lambda m: int(m["internalDate"]), reverse=True):
            code = parse_steam_code(message, login, earliest, search_settings=search_settings)
            if code:
                return code, message["id"]
        return None

    async def latest_code_for_accounts(self, credentials, accounts, earliest, search_settings=None):
        token = (
            await self.token(
                grant_type="refresh_token",
                refresh_token=credentials["refresh_token"],
            )
        )["access_token"]
        settings = _normalized_parser_settings(search_settings)
        query = f"after:{int(earliest.timestamp())}"
        if settings["sender"]:
            query = f"from:{settings['sender']} " + query
        result = await self.get(
            "messages",
            token,
            q=query,
            maxResults=20,
        )
        messages = [
            await self.get("messages/" + item["id"], token, format="full")
            for item in result.get("messages", [])
        ]
        for message in sorted(messages, key=lambda item: int(item["internalDate"]), reverse=True):
            parsed = parse_latest_steam_code(
                message, accounts, earliest, search_settings=search_settings
            )
            if parsed:
                return parsed
        return None
