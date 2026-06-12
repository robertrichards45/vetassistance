import os, secrets
from werkzeug.utils import secure_filename

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def org_root(storage_root: str, org_id: int) -> str:
    p = os.path.join(storage_root, f"org_{org_id}")
    ensure_dir(p)
    return p

def client_root(storage_root: str, org_id: int, client_id: int) -> str:
    p = os.path.join(org_root(storage_root, org_id), f"client_{client_id}")
    ensure_dir(p)
    return p

def save_upload(storage_root: str, org_id: int, client_id: int, file_storage) -> tuple[str,str]:
    cr = client_root(storage_root, org_id, client_id)
    fname = secure_filename(file_storage.filename or "upload.bin")
    token = secrets.token_hex(8)
    final_name = f"{token}__{fname}"
    full_path = os.path.join(cr, final_name)
    file_storage.save(full_path)
    return full_path, final_name
