import os
import stripe
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from app.routers.provision import _run_provisioning, ProvisionRequest, CompanyInfo, set_status

router = APIRouter()

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_REPLACE_ME")


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})

        customer_slug = metadata.get("customer_slug")
        package = metadata.get("package")
        host_port = int(metadata.get("host_port", 0))

        company_fields = {
            k.replace("company_", ""): v
            for k, v in metadata.items()
            if k.startswith("company_")
        }

        req = ProvisionRequest(
            customer_slug=customer_slug,
            package=package,
            company_info=CompanyInfo(**company_fields) if company_fields else None,
        )

        set_status(customer_slug, {"state": "queued"})
        background_tasks.add_task(_run_provisioning, req, host_port)

    return {"received": True}
