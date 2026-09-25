from cryptography.fernet import InvalidToken
from sqlalchemy import select

from app.models import GmailMailbox, Product

DEFAULT_SEARCH_SETTINGS = {
    "code_limit": None,
    "max_age_minutes": 60,
    "code_length": 5,
    "allow_spaces": False,
    "require_login": True,
    "code_type": "alnum",
    "body_keyword": "Steam Guard",
    "sender": "noreply@steampowered.com",
    "subject": "",
}


def mailbox_search_settings(mailbox: GmailMailbox | None):
    """Return a complete, validated settings snapshot for a mailbox."""
    result = DEFAULT_SEARCH_SETTINGS.copy()
    raw = mailbox.search_settings if mailbox and isinstance(mailbox.search_settings, dict) else {}
    if isinstance(raw.get("code_limit"), int) and 0 <= raw["code_limit"] <= 20:
        result["code_limit"] = raw["code_limit"]
    if isinstance(raw.get("max_age_minutes"), int) and 1 <= raw["max_age_minutes"] <= 1440:
        result["max_age_minutes"] = raw["max_age_minutes"]
    if isinstance(raw.get("code_length"), int) and 1 <= raw["code_length"] <= 64:
        result["code_length"] = raw["code_length"]
    if isinstance(raw.get("allow_spaces"), bool):
        result["allow_spaces"] = raw["allow_spaces"]
    if isinstance(raw.get("require_login"), bool):
        result["require_login"] = raw["require_login"]
    if raw.get("code_type") in {"alnum", "letters", "digits"}:
        result["code_type"] = raw["code_type"]
    for key in ("body_keyword", "sender", "subject"):
        if isinstance(raw.get(key), str) and len(raw[key]) <= 200:
            result[key] = raw[key].strip()
    return result


async def mailbox_for_product(session, product: Product):
    if product.gmail_mailbox_id:
        return await session.get(GmailMailbox, product.gmail_mailbox_id)
    return None


async def effective_code_limit(session, product: Product):
    settings = mailbox_search_settings(await mailbox_for_product(session, product))
    return product.code_limit if settings["code_limit"] is None else settings["code_limit"]


async def credentials_for_product(session, product: Product, vault):
    """Resolve shared credentials, with a safe fallback for pre-migration products."""
    if product.gmail_mailbox_id:
        mailbox = await session.get(GmailMailbox, product.gmail_mailbox_id)
        if mailbox:
            return vault.unpack(mailbox.credentials_encrypted)
    if product.gmail_credentials_encrypted:
        return vault.unpack(product.gmail_credentials_encrypted)
    return None


async def save_mailbox(session, vault, packed_credentials: str):
    credentials = vault.unpack(packed_credentials)
    email = credentials["email"].strip().lower()
    mailbox = await session.scalar(select(GmailMailbox).where(GmailMailbox.email == email))
    if mailbox is None:
        mailbox = GmailMailbox(email=email, credentials_encrypted=packed_credentials)
        session.add(mailbox)
        await session.flush()
    else:
        # A reconnect rotates credentials once for every product assigned to this mailbox.
        mailbox.credentials_encrypted = packed_credentials
    return mailbox


async def import_legacy_mailboxes(session, vault):
    """Lazily consolidate credentials stored by older releases without losing data."""
    products = (
        await session.scalars(
            select(Product).where(
                Product.gmail_mailbox_id.is_(None),
                Product.gmail_credentials_encrypted.is_not(None),
            )
        )
    ).all()
    imported = 0
    for product in products:
        try:
            mailbox = await save_mailbox(session, vault, product.gmail_credentials_encrypted)
        except (AttributeError, InvalidToken, KeyError, TypeError, ValueError):
            continue
        product.gmail_mailbox_id = mailbox.id
        imported += 1
    if imported:
        await session.flush()
    return imported
