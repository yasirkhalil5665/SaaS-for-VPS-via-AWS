import shutil
import subprocess
from pathlib import Path

from app.services.nginx_manager import remove_nginx_config

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOMERS_DIR = BASE_DIR / "customers"


def deprovision_customer(customer_slug: str, remove_data: bool = True) -> dict:
    customer_dir = CUSTOMERS_DIR / customer_slug
    compose_path = customer_dir / "docker-compose.yml"

    docker_result = None
    if compose_path.exists():
        args = ["docker", "compose", "-f", str(compose_path), "down"]
        if remove_data:
            args.append("-v")  # also remove named volumes (data, config, log)
        result = subprocess.run(args, cwd=str(customer_dir), capture_output=True, text=True)
        docker_result = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    nginx_result = remove_nginx_config(customer_slug)

    dir_removed = False
    if remove_data and customer_dir.exists():
        shutil.rmtree(customer_dir)
        dir_removed = True

    return {
        "customer_slug": customer_slug,
        "docker": docker_result,
        "nginx": nginx_result,
        "directory_removed": dir_removed,
    }
