from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse

from app.services.backup_manager import (
    create_backup, list_backups, restore_backup, delete_backup, get_backup_path,
)
from app.services.auth_store import verify_credentials

router = APIRouter()


def _check_auth(customer_slug: str, x_instance_password: str | None):
    if not x_instance_password or not verify_credentials(customer_slug, x_instance_password):
        raise HTTPException(status_code=401, detail="Invalid or missing instance password")


@router.get("/instances/{customer_slug}/backups")
def get_backups(customer_slug: str, x_instance_password: str | None = Header(default=None)):
    _check_auth(customer_slug, x_instance_password)
    return {"backups": list_backups(customer_slug)}


@router.post("/instances/{customer_slug}/backups")
def post_backup(customer_slug: str, x_instance_password: str | None = Header(default=None)):
    _check_auth(customer_slug, x_instance_password)
    result = create_backup(customer_slug)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Backup failed"))
    return result


@router.post("/instances/{customer_slug}/backups/{filename}/restore")
def post_restore(customer_slug: str, filename: str, x_instance_password: str | None = Header(default=None)):
    _check_auth(customer_slug, x_instance_password)
    result = restore_backup(customer_slug, filename)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Restore failed"))
    return result


@router.get("/instances/{customer_slug}/backups/{filename}/download")
def download_backup(customer_slug: str, filename: str, x_instance_password: str | None = Header(default=None)):
    _check_auth(customer_slug, x_instance_password)
    path = get_backup_path(customer_slug, filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup file not found")
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


@router.delete("/instances/{customer_slug}/backups/{filename}")
def remove_backup(customer_slug: str, filename: str, x_instance_password: str | None = Header(default=None)):
    _check_auth(customer_slug, x_instance_password)
    result = delete_backup(customer_slug, filename)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Delete failed"))
    return result
