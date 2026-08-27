from dotenv import load_dotenv
load_dotenv()  # loads .env file from project root, if present, before anything else runs

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.provision import router as provision_router
from app.routers.signup import router as signup_router
from app.routers.checkout import router as checkout_router
from app.routers.webhook import router as webhook_router
from app.routers.backups import router as backups_router
from app.routers.invoices import router as invoices_router
from app.routers.instances import router as instances_router
from app.routers.admin import router as admin_router
from app.services.main_site_sync import MAIN_SITE_DB

app = FastAPI(title="Odoo Provisioning Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to your real domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(provision_router)
app.include_router(signup_router)
app.include_router(checkout_router)
app.include_router(webhook_router)
# backups_router and invoices_router registered BEFORE instances_router: their
# literal "/backups" and "/invoices" path segments must be matched before
# instances_router's catch-all "/{action}" route swallows the request instead.
app.include_router(backups_router)
app.include_router(invoices_router)
app.include_router(instances_router)
app.include_router(admin_router)


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/config")
def public_config():
    """Read-only, non-secret config the signup page needs at runtime -
    specifically MAIN_SITE_DB, so it can call the main Odoo site's own
    /web/session/authenticate with the CORRECT database name after
    provisioning finishes, instead of a hardcoded guess baked into the
    static HTML file that silently goes stale the moment .env changes.
    A mismatch here was the main reason auto-login kept falling back to
    showing /web/login instead of skipping it."""
    return {"main_site_db": MAIN_SITE_DB}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
