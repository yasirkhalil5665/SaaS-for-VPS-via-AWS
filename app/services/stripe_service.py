import os
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_REPLACE_ME")

# Map package -> Stripe Price ID (create these in your Stripe Dashboard test mode)
PACKAGE_PRICE_IDS = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", "price_REPLACE_STARTER"),
    "business": os.environ.get("STRIPE_PRICE_BUSINESS", "price_REPLACE_BUSINESS"),
    "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE", "price_REPLACE_ENTERPRISE"),
}

SUCCESS_URL = os.environ.get("CHECKOUT_SUCCESS_URL", "https://api.coolbites.site/checkout/success")
CANCEL_URL = os.environ.get("CHECKOUT_CANCEL_URL", "https://api.coolbites.site/checkout/cancel")


def create_checkout_session(customer_slug: str, package: str, host_port: int, company_info: dict | None) -> str:
    if package not in PACKAGE_PRICE_IDS:
        raise ValueError(f"Unknown package: {package}")

    metadata = {
        "customer_slug": customer_slug,
        "package": package,
        "host_port": str(host_port),
    }
    if company_info:
        # Stripe metadata values must be strings, keep it flat and simple
        for k, v in company_info.items():
            if v is not None:
                metadata[f"company_{k}"] = str(v)

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": PACKAGE_PRICE_IDS[package], "quantity": 1}],
        success_url=SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=CANCEL_URL,
        metadata=metadata,
    )
    return session.url
