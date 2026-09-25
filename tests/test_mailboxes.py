from sqlalchemy import select

from app.mailboxes import credentials_for_product, import_legacy_mailboxes, save_mailbox
from app.models import GmailMailbox, Product


async def test_legacy_products_are_consolidated_by_email(shop):
    async with shop.sessions() as session, session.begin():
        second = Product(
            name_ua="Інша гра",
            name_ru="Інша гра",
            price=10000,
            steam_login_encrypted=shop.vault.encrypt("second"),
            steam_password_encrypted=shop.vault.encrypt("password"),
            gmail_credentials_encrypted=shop.vault.pack(
                {"email": "TEST@gmail.com", "refresh_token": "newest"}
            ),
        )
        session.add(second)

    async with shop.sessions() as session, session.begin():
        assert await import_legacy_mailboxes(session, shop.vault) == 2

    async with shop.sessions() as session:
        products = (await session.scalars(select(Product).order_by(Product.id))).all()
        mailboxes = (await session.scalars(select(GmailMailbox))).all()
        assert len(mailboxes) == 1
        assert products[0].gmail_mailbox_id == products[1].gmail_mailbox_id == mailboxes[0].id
        assert (await credentials_for_product(session, products[0], shop.vault))["email"] == "TEST@gmail.com"


async def test_reconnect_updates_one_shared_mailbox(shop):
    async with shop.sessions() as session, session.begin():
        mailbox = await save_mailbox(
            session,
            shop.vault,
            shop.vault.pack({"email": "shared@gmail.com", "refresh_token": "old"}),
        )
        mailbox_id = mailbox.id
        await save_mailbox(
            session,
            shop.vault,
            shop.vault.pack({"email": "SHARED@gmail.com", "refresh_token": "new"}),
        )

    async with shop.sessions() as session:
        mailboxes = (await session.scalars(select(GmailMailbox))).all()
        assert len(mailboxes) == 1
        assert mailboxes[0].id == mailbox_id
        assert shop.vault.unpack(mailboxes[0].credentials_encrypted)["refresh_token"] == "new"


async def test_invalid_legacy_credentials_do_not_block_mailbox_import(shop):
    async with shop.sessions() as session, session.begin():
        session.add(
            Product(
                name_ua="Invalid legacy Gmail",
                name_ru="Invalid legacy Gmail",
                price=10000,
                steam_login_encrypted=shop.vault.encrypt("legacy"),
                steam_password_encrypted=shop.vault.encrypt("password"),
                gmail_credentials_encrypted="not-a-valid-fernet-token",
            )
        )

    async with shop.sessions() as session, session.begin():
        assert await import_legacy_mailboxes(session, shop.vault) == 1

    async with shop.sessions() as session:
        products = (await session.scalars(select(Product).order_by(Product.id))).all()
        assert products[0].gmail_mailbox_id is not None
        assert products[1].gmail_mailbox_id is None


async def test_legacy_credentials_without_email_do_not_block_mailbox_import(shop):
    async with shop.sessions() as session, session.begin():
        product = await session.get(Product, 1)
        product.gmail_credentials_encrypted = shop.vault.pack(
            {"email": None, "refresh_token": "secret"}
        )

    async with shop.sessions() as session, session.begin():
        assert await import_legacy_mailboxes(session, shop.vault) == 0
