from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.provision import router as provision_router
from app.routers.checkout import router as checkout_router
from app.routers.webhook import router as webhook_router

app = FastAPI(title="Odoo Provisioning Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to your real domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(provision_router)
app.include_router(checkout_router)
app.include_router(webhook_router)


@app.get("/")
def root():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
