# dvi_status.py

import requests

DVI_BASE = "https://dviapi.rowriter.com"

# ---------------------------------------------------------
# ChangeStatus → 3 / 4 / 5
# ---------------------------------------------------------
def change_status(token: str, ro_number: str, status: str, ro_type: str = "R"):
    """
    Change RO status in DVI.

    Status codes:
        3 = Start Inspection
        4 = ISO Complete
        5 = PMA Complete
    """

    url = f"{DVI_BASE}/ChangeStatus"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "Status": status,
        "Type": ro_type,
        "RONumber": ro_number
    }

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text
