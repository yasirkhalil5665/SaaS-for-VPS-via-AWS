from fastapi import FastAPI

from app.routers.provision import router as provision_router

app = FastAPI(title="Odoo Provisioning Engine")

app.include_router(provision_router)


@app.get("/")
def root():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
