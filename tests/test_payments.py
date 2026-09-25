from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.models import (
    CartItem,
    Order,
    PaymentCard,
    PaymentEvent,
    PaymentReceipt,
    Product,
    PromoCode,
    Referral,
    User,
    now,
)
from app.services import (
    ShopError,
    cleanup_expired_promo_codes,
    delete_promo_code,
    has_recent_purchase,
    register_referral,
    set_setting,
)
from app.worker import deliver_one


async def test_repeated_webhook_delivers_once(shop, payment):
    await shop.payment(payment, "digest1")
    await shop.payment(payment, "digest1")
    await shop.payment(payment, "digest2")
    bot = AsyncMock()
    bot.send_message.return_value.message_id = 123
    assert await deliver_one(shop, bot)
    assert not await deliver_one(shop, bot)
    assert bot.send_message.await_count == 1
    assert "password&lt;&amp;&gt;" in bot.send_message.call_args.args[1]
    async with shop.sessions() as session:
        order = await session.get(Order, "a" * 32)
        assert order.status == "delivered"
        assert order.delivered_at and order.delivery_message_id == 123
        assert await session.scalar(select(func.count()).select_from(PaymentEvent)) == 2


async def test_reward_promo_is_sent_separately_from_product(shop, payment):
    await shop.payment(payment, "reward-separate")
    async with shop.sessions() as session, session.begin():
        session.add(
            PromoCode(
                code="GIFTY",
                discount_percent=100,
                usage_limit=1,
                max_product_price=5000,
                expires_at=now() + timedelta(days=1),
                created_by=1,
                generated_for_order_id="a" * 32,
            )
        )
    bot = AsyncMock()
    bot.send_message.return_value.message_id = 123

    assert await deliver_one(shop, bot)

    assert bot.send_message.await_count == 2
    product_message, promo_message = [call.args[1] for call in bot.send_message.await_args_list]
    assert "GIFTY" not in product_message
    assert "GIFTY" in promo_message
    assert "Мої промокоди" in promo_message


async def test_referral_visits_are_visible_but_only_eligible_users_count(shop):
    async with shop.sessions() as session, session.begin():
        new_referrer = await session.get(User, 1)
        existing_referred = await session.get(User, 2)
        eligible_referred = User(id=3, language="ua", first_name="Eligible")
        other_referrer = User(id=4, language="ua", first_name="Referrer")
        session.add_all([eligible_referred, other_referrer])
        await session.flush()

        assert not await register_referral(
            session, other_referrer, other_referrer.id, is_new_user=True
        )
        assert await register_referral(
            session, existing_referred, other_referrer.id, is_new_user=False
        )
        assert await register_referral(
            session, eligible_referred, new_referrer.id, is_new_user=True
        )
        assert not await register_referral(
            session, eligible_referred, new_referrer.id, is_new_user=True
        )

        existing_referral = await session.get(Referral, existing_referred.id)
        eligible_referral = await session.get(Referral, eligible_referred.id)
        assert not existing_referral.eligible
        assert existing_referral.ineligible_reason == "existing_user"
        assert eligible_referral.eligible
        assert eligible_referral.ineligible_reason is None


async def test_disabled_referral_system_does_not_register_referrals(shop):
    async with shop.sessions() as session, session.begin():
        referrer = await session.get(User, 1)
        referred = User(id=99, language="ua", first_name="Referred")
        session.add(referred)
        await session.flush()
        await set_setting(session, "referrals_enabled", "false")

        assert not await register_referral(session, referred, referrer.id, is_new_user=True)
        assert await session.get(Referral, referred.id) is None


async def test_first_referral_purchase_rewards_both_users_once(shop, payment):
    order_id = "b" * 32
    async with shop.sessions() as session, session.begin():
        await set_setting(session, "referral_promo_discount_percent", "25")
        await set_setting(session, "referral_promo_usage_limit", "2")
        await set_setting(session, "referral_promo_max_product_price", "12_300")
        await set_setting(session, "referral_promo_lifetime_days", "7")
        await set_setting(session, "referral_promo_code_length", "8")
        session.add(
            User(
                id=3,
                language="ua",
                first_name="Referrer",
                created_at=now() - timedelta(days=31),
            )
        )
        session.add(Referral(referrer_id=3, referred_id=2))
        session.add(
            Order(
                id=order_id,
                user_id=2,
                product_id=1,
                price_snapshot=49900,
                product_name_snapshot="Referral purchase",
                mono_invoice_id="invoice2",
            )
        )

    referral_payment = dict(payment)
    referral_payment.update(invoiceId="invoice2", reference=order_id)
    await shop.payment(referral_payment, "referral-first")
    await shop.payment(referral_payment, "referral-repeat")

    async with shop.sessions() as session:
        promos = (
            await session.scalars(select(PromoCode).where(PromoCode.generated_for_order_id == order_id))
        ).all()
        referral = await session.get(Referral, 2)
        assert {promo.created_by for promo in promos} == {2, 3}
        assert len(promos) == 2
        assert {promo.reward_source for promo in promos} == {"referral"}
        assert {promo.discount_percent for promo in promos} == {25}
        assert {promo.usage_limit for promo in promos} == {2}
        assert {promo.max_product_price for promo in promos} == {12_300}
        assert {len(promo.code) for promo in promos} == {8}
        assert referral.rewarded_at is not None
        assert referral.first_purchase_order_id == order_id

    bot = AsyncMock()
    bot.send_message.return_value.message_id = 123
    assert await deliver_one(shop, bot)
    recipients = [call.args[0] for call in bot.send_message.await_args_list]
    assert recipients.count(2) == 2
    assert recipients.count(3) == 1


@pytest.mark.parametrize(
    "field,value", [("amount", 1), ("amount", "49900"), ("ccy", 840), ("reference", "wrong")]
)
async def test_mismatched_payment_never_grants_access(shop, payment, field, value):
    payment[field] = value
    await shop.payment(payment, "mismatch")
    async with shop.sessions() as session:
        assert (await session.get(Order, "a" * 32)).status == "waiting_payment"
        assert (await session.scalar(select(PaymentEvent))).outcome == "mismatch"


async def test_payment_amount_within_five_hryvnias_is_accepted(shop, payment):
    payment["amount"] += 500
    await shop.payment(payment, "within-tolerance")
    async with shop.sessions() as session:
        assert (await session.get(Order, "a" * 32)).status == "paid"


async def test_out_of_order_events_do_not_undo_payment(shop, payment):
    await shop.payment(payment, "success")
    payment.update(status="failure", modifiedDate="2026-09-09T11:59:00Z")
    await shop.payment(payment, "old")
    payment["modifiedDate"] = "2026-09-09T12:01:00Z"
    await shop.payment(payment, "later_failure")
    async with shop.sessions() as session:
        assert (await session.get(Order, "a" * 32)).status == "paid"


async def test_unknown_invoice(shop, payment):
    payment.update(invoiceId="unknown", reference="unknown")
    with pytest.raises(LookupError):
        await shop.payment(payment, "unknown")


async def test_callback_recovers_invoice_creation_timeout(shop, payment):
    async with shop.sessions() as session, session.begin():
        order = await session.get(Order, "a" * 32)
        order.mono_invoice_id, order.status = None, "payment_failed"
    await shop.payment(payment, "recovered")
    async with shop.sessions() as session:
        order = await session.get(Order, "a" * 32)
        assert order.status == "paid" and order.mono_invoice_id == "invoice1"


async def test_ambiguous_delivery_is_not_retried(shop, payment):
    await shop.payment(payment, "success")
    bot = AsyncMock()
    bot.send_message.side_effect = TimeoutError()
    assert await deliver_one(shop, bot)
    assert not await deliver_one(shop, bot)
    async with shop.sessions() as session:
        order = await session.get(Order, "a" * 32)
        assert order.status == "paid" and order.delivery_uncertain


async def test_checkout_snapshot_and_repeat_click(shop):
    shop.mono.create.return_value = {"invoiceId": "new", "pageUrl": "https://pay.test/new"}
    order = await shop.checkout(1, 1)
    async with shop.sessions() as session, session.begin():
        product = await session.get(Product, 1)
        product.price, product.name_ua = 59900, "Changed"
    repeated = await shop.checkout(1, 1)
    assert repeated.id == order.id
    assert repeated.price_snapshot == 49900 and repeated.product_name_snapshot == "Гра"
    assert shop.mono.create.await_count == 1


async def test_sale_applies_fifty_percent_to_checkout(shop):
    async with shop.sessions() as session, session.begin():
        await set_setting(session, "sale_enabled", "true")
    shop.mono.create.return_value = {"invoiceId": "sale", "pageUrl": "https://pay.test/sale"}

    order = await shop.checkout(2, 1)

    assert order.original_price_snapshot == 49900
    assert order.discount_percent_snapshot == 50
    assert order.price_snapshot == 25000


async def test_cart_creates_one_payment_and_delivers_each_product(shop):
    async with shop.sessions() as session, session.begin():
        second = Product(
            name_ua="Друга гра",
            name_ru="Другая игра",
            price=10100,
            delivery_mode="manual",
        )
        session.add(second)
        await session.flush()
        session.add_all([CartItem(user_id=1, product_id=1), CartItem(user_id=1, product_id=second.id)])
    shop.mono.create.return_value = {"invoiceId": "cart-invoice", "pageUrl": "https://pay.test/cart"}

    primary = await shop.checkout_cart(1)

    assert primary.payment_total_snapshot == 60000
    assert shop.mono.create.await_args.args[0].id == primary.id
    async with shop.sessions() as session:
        orders = (await session.scalars(select(Order).where(Order.checkout_id == primary.checkout_id))).all()
        assert len(orders) == 2
        assert (
            await session.scalar(select(PromoCode).where(PromoCode.generated_for_order_id == primary.id))
            is None
        )
        assert (
            await session.scalar(select(func.count()).select_from(CartItem).where(CartItem.user_id == 1)) == 2
        )

    await shop.payment(
        {
            "invoiceId": "cart-invoice",
            "reference": primary.id,
            "amount": 60000,
            "ccy": 980,
            "status": "success",
            "modifiedDate": "2026-09-24T12:00:00Z",
        },
        "cart-payment-digest",
    )
    async with shop.sessions() as session:
        statuses = (
            await session.scalars(select(Order.status).where(Order.checkout_id == primary.checkout_id))
        ).all()
        assert statuses == ["paid", "paid"]
        reward = await session.scalar(select(PromoCode).where(PromoCode.generated_for_order_id == primary.id))
        assert reward is not None
        assert len(reward.code) == 5
        assert reward.discount_percent == 100
        assert reward.max_product_price == 5000
        assert await session.scalar(
            select(func.count()).select_from(CartItem).where(CartItem.user_id == 1)
        ) == 0


async def test_cancelled_cart_payment_keeps_cart_items(shop):
    async with shop.sessions() as session, session.begin():
        second = Product(
            name_ua="Друга гра",
            name_ru="Другая игра",
            price=10100,
            delivery_mode="manual",
        )
        session.add(second)
        await session.flush()
        session.add_all([CartItem(user_id=1, product_id=1), CartItem(user_id=1, product_id=second.id)])
    shop.mono.create.return_value = {
        "invoiceId": "cancelled-cart-invoice",
        "pageUrl": "https://pay.test/cancelled-cart",
    }

    primary = await shop.checkout_cart(1)
    await shop.cancel_payment(primary.id, 1)

    async with shop.sessions() as session:
        assert await session.scalar(
            select(func.count()).select_from(CartItem).where(CartItem.user_id == 1)
        ) == 2
        statuses = (
            await session.scalars(select(Order.status).where(Order.checkout_id == primary.checkout_id))
        ).all()
        assert statuses == ["cancelled", "cancelled"]


async def test_cart_applies_tip_and_promo_once(shop):
    async with shop.sessions() as session, session.begin():
        second = Product(name_ua="Друга", name_ru="Вторая", price=10100, delivery_mode="manual")
        promo = PromoCode(
            code="CART10",
            discount_percent=10,
            usage_limit=1,
            max_product_price=100_000,
            expires_at=now() + timedelta(hours=1),
            created_by=99,
        )
        session.add_all([second, promo])
        await session.flush()
        session.add_all([CartItem(user_id=1, product_id=1), CartItem(user_id=1, product_id=second.id)])
    shop.mono.create.return_value = {"invoiceId": "cart-tip", "pageUrl": "https://pay.test/cart-tip"}

    primary = await shop.checkout_cart(1, tip_percent=15, promo_code="CART10")

    assert primary.payment_total_snapshot == 63200
    async with shop.sessions() as session:
        orders = (await session.scalars(select(Order).where(Order.checkout_id == primary.checkout_id))).all()
        assert sum(order.promo_code_id is not None for order in orders) == 1
        assert all(order.tip_percent_snapshot == 15 for order in orders)


async def test_cart_promo_makes_only_first_eligible_product_free(shop):
    async with shop.sessions() as session, session.begin():
        cheap = Product(name_ua="Дешева", name_ru="Дешевая", price=4900, delivery_mode="manual")
        promo = PromoCode(
            code="ONEFREE",
            discount_percent=100,
            usage_limit=1,
            max_product_price=5000,
            expires_at=now() + timedelta(hours=1),
            created_by=99,
        )
        session.add_all([cheap, promo])
        await session.flush()
        session.add_all([CartItem(user_id=1, product_id=1), CartItem(user_id=1, product_id=cheap.id)])
    shop.mono.create.return_value = {"invoiceId": "one-free", "pageUrl": "https://pay.test/one-free"}

    primary = await shop.checkout_cart(1, promo_code="ONEFREE")

    assert primary.payment_total_snapshot == 49900
    async with shop.sessions() as session:
        orders = (await session.scalars(select(Order).where(Order.checkout_id == primary.checkout_id))).all()
        discounted = next(order for order in orders if order.product_id != 1)
        assert discounted.price_snapshot == 0
        assert discounted.promo_code_snapshot == "ONEFREE"


@pytest.mark.parametrize(
    ("price", "reward_promos_enabled", "reward_expected"),
    [(14_600, True, False), (14_700, True, True), (14_700, False, False)],
)
async def test_cart_reward_starts_at_147_hryvnias(shop, price, reward_promos_enabled, reward_expected):
    async with shop.sessions() as session, session.begin():
        product = await session.get(Product, 1)
        product.price = price
        if not reward_promos_enabled:
            await set_setting(session, "cart_reward_promos_enabled", "false")
        session.add(CartItem(user_id=1, product_id=1))
    shop.mono.create.return_value = {
        "invoiceId": f"threshold-{price}",
        "pageUrl": "https://pay.test/threshold",
    }
    primary = await shop.checkout_cart(1)

    await shop.payment(
        {
            "invoiceId": f"threshold-{price}",
            "reference": primary.id,
            "amount": price,
            "ccy": 980,
            "status": "success",
            "modifiedDate": "2026-09-24T12:00:00Z",
        },
        f"threshold-digest-{price}",
    )

    async with shop.sessions() as session:
        reward = await session.scalar(select(PromoCode).where(PromoCode.generated_for_order_id == primary.id))
        assert (reward is not None) is reward_expected


async def test_free_promo_delivers_without_creating_a_payment(shop):
    async with shop.sessions() as session, session.begin():
        product = await session.get(Product, 1)
        product.reward_promo_enabled = True  # Legacy flag must no longer issue a promo.
        session.add(
            PromoCode(
                code="FREE100",
                discount_percent=100,
                usage_limit=1,
                max_product_price=100_000,
                expires_at=now() + timedelta(hours=1),
                created_by=99,
            )
        )

    order = await shop.checkout(1, 1, promo_code="FREE100")
    repeated = await shop.checkout(1, 1, promo_code="FREE100")

    assert order.id == repeated.id
    assert order.status == "delivered"
    assert order.payment_method == "free"
    assert order.price_snapshot == 0
    shop.mono.create.assert_not_awaited()
    async with shop.sessions() as session:
        assert (
            await session.scalar(select(PromoCode).where(PromoCode.generated_for_order_id == order.id))
            is None
        )


async def test_free_promo_skips_payment_choice_in_hybrid_mode(shop):
    async with shop.sessions() as session, session.begin():
        await set_setting(session, "payment_mode", "hybrid")
        session.add(
            PromoCode(
                code="HYBRIDFREE100",
                discount_percent=100,
                usage_limit=1,
                max_product_price=100_000,
                expires_at=now() + timedelta(hours=1),
                created_by=99,
            )
        )

    order = await shop.checkout(1, 1, promo_code="HYBRIDFREE100")

    assert order.status == "delivered"
    assert order.payment_method == "free"
    assert order.price_snapshot == 0
    shop.mono.create.assert_not_awaited()


async def test_manual_promo_deletion_preserves_order_snapshot(shop):
    async with shop.sessions() as session, session.begin():
        promo = PromoCode(
            code="DELETE10",
            discount_percent=10,
            usage_limit=10,
            max_product_price=100_000,
            expires_at=now() + timedelta(hours=1),
            created_by=99,
        )
        session.add(promo)
        await session.flush()
        promo_id = promo.id
        order = await session.get(Order, "a" * 32)
        order.promo_code_id = promo_id
        order.promo_code_snapshot = promo.code
        order.promo_discount_percent_snapshot = promo.discount_percent

    async with shop.sessions() as session, session.begin():
        assert await delete_promo_code(session, promo_id) == 1

    async with shop.sessions() as session:
        order = await session.get(Order, "a" * 32)
        assert await session.get(PromoCode, promo_id) is None
        assert order.promo_code_id is None
        assert order.promo_code_snapshot == "DELETE10"
        assert order.promo_discount_percent_snapshot == 10


async def test_expired_promo_codes_are_deleted(shop):
    async with shop.sessions() as session, session.begin():
        expired = PromoCode(
            code="EXPIRED10",
            discount_percent=10,
            usage_limit=10,
            max_product_price=100_000,
            expires_at=now() - timedelta(seconds=1),
            created_by=99,
        )
        active = PromoCode(
            code="ACTIVE10",
            discount_percent=10,
            usage_limit=10,
            max_product_price=100_000,
            expires_at=now() + timedelta(hours=1),
            created_by=99,
        )
        session.add_all([expired, active])
        await session.flush()
        expired_id, active_id = expired.id, active.id

    async with shop.sessions() as session, session.begin():
        assert await cleanup_expired_promo_codes(session) == 1

    async with shop.sessions() as session:
        assert await session.get(PromoCode, expired_id) is None
        assert await session.get(PromoCode, active_id) is not None


async def test_maintenance_and_hidden_product(shop):
    async with shop.sessions() as session, session.begin():
        await set_setting(session, "enabled", "false")
    with pytest.raises(ShopError, match="maintenance"):
        await shop.checkout(1, 1)
    async with shop.sessions() as session, session.begin():
        await set_setting(session, "enabled", "true")
        (await session.get(Product, 1)).visible = False
    with pytest.raises(ShopError, match="missing"):
        await shop.checkout(1, 1)
    shop.mono.create.assert_not_awaited()


async def test_checkout_allows_product_without_gmail(shop):
    async with shop.sessions() as session, session.begin():
        (await session.get(Product, 1)).gmail_credentials_encrypted = None
    shop.mono.create.return_value = {"invoiceId": "without-mail", "pageUrl": "https://pay.test/no-mail"}
    order = await shop.checkout(1, 1)
    assert order.mono_invoice_id == "without-mail"


async def test_deepseek_checkout_snapshots_active_cards(shop):
    async with shop.sessions() as session, session.begin():
        await set_setting(session, "payment_mode", "deepseek")
        await set_setting(
            session,
            "receipt_iban",
            shop.vault.encrypt("UA123456789012345678901234567"),
        )
        session.add_all(
            [
                PaymentCard(
                    label="Mono", number_encrypted=shop.vault.encrypt("4441111043425077"), last4="5077"
                ),
                PaymentCard(
                    label="Other", number_encrypted=shop.vault.encrypt("4111111111111234"), last4="1234"
                ),
            ]
        )
    order = await shop.checkout(1, 1)
    assert order.payment_method == "receipt"
    cards = shop.vault.unpack(order.payment_cards_encrypted)["cards"]
    assert [(card["label"], card["last4"]) for card in cards] == [("Mono", "5077"), ("Other", "1234")]
    assert shop.vault.unpack(order.payment_cards_encrypted)["ibans"] == ["UA123456789012345678901234567"]
    shop.mono.create.assert_not_awaited()


async def test_hybrid_mode_requires_and_respects_buyer_choice(shop):
    async with shop.sessions() as session, session.begin():
        await set_setting(session, "payment_mode", "hybrid")
        session.add(
            PaymentCard(
                label="Card",
                number_encrypted=shop.vault.encrypt("4441111043425077"),
                last4="5077",
            )
        )
    with pytest.raises(ShopError, match="missing"):
        await shop.checkout(1, 1)

    shop.mono.create.return_value = {
        "invoiceId": "hybrid-mono",
        "pageUrl": "https://pay.test/hybrid-mono",
    }
    mono_order = await shop.checkout(1, 1, "mono")
    assert mono_order.payment_method == "acquiring"
    assert mono_order.mono_invoice_id == "hybrid-mono"

    receipt_order = await shop.checkout(2, 1, "deepseek")
    assert receipt_order.payment_method == "receipt"
    cards = shop.vault.unpack(receipt_order.payment_cards_encrypted)["cards"]
    assert cards[0]["last4"] == "5077"


async def test_payment_message_is_saved_without_animation(shop):
    async with shop.sessions() as session, session.begin():
        order = await session.get(Order, "a" * 32)
        order.payment_url = "https://pay.test/invoice1"
    await shop.attach_payment_message("a" * 32, 1, 321)
    async with shop.sessions() as session:
        order = await session.get(Order, "a" * 32)
        assert order.payment_message_id == 321
        assert order.payment_animation_step == 0
        assert order.payment_animation_at is None


async def test_user_can_cancel_waiting_payment(shop):
    order = await shop.cancel_payment("a" * 32, 1)
    assert order.status == "cancelled"
    async with shop.sessions() as session:
        assert (await session.get(Order, "a" * 32)).status == "cancelled"
    with pytest.raises(ShopError, match="missing"):
        await shop.cancel_payment("a" * 32, 1)


async def test_manual_receipt_review_blocks_checkout_and_cancellation(shop):
    async with shop.sessions() as session, session.begin():
        session.add(
            PaymentReceipt(
                order_id="a" * 32,
                user_id=1,
                telegram_file_id="photo",
                file_sha256="hash",
                status="manual_review",
            )
        )
    with pytest.raises(ShopError, match="receipt_pending"):
        await shop.checkout(1, 1)
    with pytest.raises(ShopError, match="receipt_locked"):
        await shop.cancel_payment("a" * 32, 1)


async def test_recent_purchase_requires_manual_receipt_review(shop):
    checked_at = now()
    async with shop.sessions() as session, session.begin():
        previous = await session.get(Order, "a" * 32)
        previous.status = "delivered"
        previous.paid_at = checked_at
        assert await has_recent_purchase(session, 1, "b" * 32, checked_at)
        assert not await has_recent_purchase(session, 2, "b" * 32, checked_at)
