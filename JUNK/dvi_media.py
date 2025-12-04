# dvi_media.py

import uuid
import requests

ROW_API_BASE = "https://api.rowriter.com"


# ---------------------------------------------------------
# SaveMedia → upload a file (local path OR bytes) to R.O. Writer blob storage
# ---------------------------------------------------------
def save_media(token: str, image_path: str = None, image_bytes: bytes = None) -> str:
    """
    Uploads image content to R.O. Writer BlobStorage.

    Supports:
      - image_path (string path to file)
      - image_bytes (raw bytes, e.g., base64-decoded)

    Returns:
      blob_name (string)
    """

    if not image_path and not image_bytes:
        raise ValueError("Must provide either image_path or image_bytes")

    blob_name = f"{uuid.uuid4().hex}.jpg"

    url = f"{ROW_API_BASE}/v2/BlobStorage/SaveMedia"
    headers = {"Authorization": f"Bearer {token}"}

    # Blob options JSON (same as your working version)
    options_json = '{"Tier":0,"Location":0,"test":null}'

    # Determine content source
    if image_path:
        file_tuple = (blob_name, open(image_path, "rb"), "image/jpeg")
    else:
        file_tuple = (blob_name, image_bytes, "image/jpeg")

    files = {
        "Options": (None, options_json, "application/json"),
        blob_name: file_tuple
    }

    resp = requests.post(url, headers=headers, files=files, timeout=60)
    resp.raise_for_status()

    # SaveMedia returns quoted string, so strip quotes:
    return resp.json().strip('"')
