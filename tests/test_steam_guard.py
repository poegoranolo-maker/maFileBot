import base64
import json

import pytest

from app.models import MailCodeRequest, Order, Product, SteamAuthenticator, now
from app.services import ShopError
from app.steam_guard import generate_steam_guard_code, parse_mafile


def test_parse_mafile_keeps_only_required_fields():
    secret = base64.b64encode(b"01234567890123456789").decode()
    parsed = parse_mafile(
        json.dumps(
            {
                "account_name": "test_account",
                "shared_secret": secret,
                "identity_secret": "must-not-be-kept",
                "revocation_code": "must-not-be-kept",
                "Session": {"SteamID": 76561198000000000, "OAuthToken": "must-not-be-kept"},
            }
        ).encode()
    )

    assert parsed == {
        "account_name": "test_account",
        "steam_id": "76561198000000000",
        "shared_secret": secret,
    }


def test_generate_steam_guard_code_is_stable_for_time_window():
    secret = base64.b64encode(b"01234567890123456789").decode()
    assert generate_steam_guard_code(secret, 1_700_000_000) == "N3FRN"
    assert generate_steam_guard_code(secret, 1_700_000_001) == "N3FRN"


def test_parse_mafile_rejects_missing_or_invalid_secret():
    with pytest.raises(ValueError, match="shared_secret"):
        parse_mafile(b'{"account_name":"test"}')
    with pytest.raises(ValueError, match="shared_secret"):
        parse_mafile(b'{"account_name":"test","shared_secret":"not-base64"}')


async def test_shop_generates_code_only_for_order_owner_and_tracks_limit(shop):
    secret = base64.b64encode(b"01234567890123456789").decode()
    async with shop.sessions() as session, session.begin():
        authenticator = SteamAuthenticator(
            account_name="test_account",
            steam_id="76561198000000000",
            shared_secret_encrypted=shop.vault.encrypt(secret),
        )
        session.add(authenticator)
        await session.flush()
        product = await session.get(Product, 1)
        product.steam_authenticator_id = authenticator.id
        product.code_limit = 1
        order = await session.get(Order, "a" * 32)
        order.status = "delivered"
        order.paid_at = now()

    code = await shop.code(1, "a" * 32)
    assert len(code) == 5
    async with shop.sessions() as session:
        request = await session.get(MailCodeRequest, 1)
        assert request.outcome == "found"

    with pytest.raises(ShopError, match="code_limit"):
        await shop.code(1, "a" * 32)
    with pytest.raises(ShopError, match="missing"):
        await shop.code(2, "a" * 32)
