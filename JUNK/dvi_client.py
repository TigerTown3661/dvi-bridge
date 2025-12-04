# dvi_client.py

import requests
from typing import List, Dict, Any, Optional

DVI_BASE = "https://dviapi.rowriter.com"


class DVIError(Exception):
    pass


def _auth_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------
# CORE DVI CALLS
# ---------------------------------------------------------

def get_ro_detail(token: str, ro_number: str) -> Dict[str, Any]:
    """
    Wrapper for your existing 'GetRODetail' (or similar) endpoint.
    Adjust the URL/path to match what you're already using.
    """
    url = f"{DVI_BASE}/GetRODetail/{ro_number}"
    resp = requests.get(url, headers=_auth_headers(token), timeout=15)
    if not resp.ok:
        raise DVIError(f"GetRODetail failed: {resp.status_code} {resp.text}")
    return resp.json()


def get_checklist_items(token: str, labor_id: str) -> Dict[str, Any]:
    """
    GET /GetCheckListItemsV2/{LaborID}/
    """
    url = f"{DVI_BASE}/GetCheckListItemsV2/{labor_id}/"
    resp = requests.get(url, headers=_auth_headers(token), timeout=15)
    if not resp.ok:
        raise DVIError(f"GetCheckListItemsV2 failed: {resp.status_code} {resp.text}")
    return resp.json()


def save_checklist(
    token: str,
    ro_number: str,
    labor_id: str,
    item_id: str,
    condition: str,
    comments: str = "",
    ro_type: str = "R",
) -> Dict[str, Any]:
    """
    POST /SaveChecklist
    """
    url = f"{DVI_BASE}/SaveChecklist"
    body = {
        "RONumber": ro_number,
        "LaborID": labor_id,
        "ItemID": item_id,
        "Comments": comments,
        "Condition": condition,
        "ROType": ro_type,
    }
    resp = requests.post(url, json=body, headers=_auth_headers(token), timeout=15)
    if not resp.ok:
        raise DVIError(f"SaveChecklist failed: {resp.status_code} {resp.text}")
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def save_checklist_image_cloud(
    token: str,
    ro_number: str,
    labor_id: str,
    item_id: str,
    blob_name: str,
    ro_type: str = "R",
) -> Dict[str, Any]:
    """
    POST /SaveChecklistImageCloud
    Media is a list of blob names (we send one at a time).
    """
    url = f"{DVI_BASE}/SaveChecklistImageCloud"
    body = {
        "RONumber": ro_number,
        "LaborID": labor_id,
        "ItemID": item_id,
        "ROType": ro_type,
        "Media": [blob_name],
        "ImageName": "",
        "Description": "",
    }
    resp = requests.post(url, json=body, headers=_auth_headers(token), timeout=15)
    if not resp.ok:
        raise DVIError(
            f"SaveChecklistImageCloud failed: {resp.status_code} {resp.text}"
        )
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def set_ro_status(token: str, ro_number: str, status: int) -> Dict[str, Any]:
    """
    POST /SetROStatus
    We already know this works in your current setup; this is the clean wrapper.
    """
    url = f"{DVI_BASE}/SetROStatus"
    body = {
        "RONumber": ro_number,
        "Status": str(status),
    }
    resp = requests.post(url, json=body, headers=_auth_headers(token), timeout=15)
    if not resp.ok:
        raise DVIError(f"SetROStatus failed: {resp.status_code} {resp.text}")
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


# ---------------------------------------------------------
# HELPERS: FIND ISO / PMA LABOR IDs FROM RO JSON
# ---------------------------------------------------------

def find_labor_id_by_description(ro_detail: Dict[str, Any], target: str) -> Optional[str]:
    """
    Case-insensitive search by 'Description' in LaborList.
    e.g., target='ISO' or 'Digital Multi-Point Vehicle Inspection'
    """
    target_lower = target.lower()
    for labor in ro_detail.get("LaborList", []):
        desc = (labor.get("Description") or "").lower()
        if target_lower in desc:
            labor_id = labor.get("ID")
            if labor_id:
                return labor_id
    return None


def find_checklist_for_name(
    ro_detail: Dict[str, Any],
    checklist_name: str,
) -> Optional[Dict[str, Any]]:
    """
    Find checklist entry by Name field in ro_detail["CheckLists"].
    """
    target = checklist_name.lower()
    for cl in ro_detail.get("CheckLists", []):
        if (cl.get("Name") or "").lower() == target:
            return cl
    return None


# ---------------------------------------------------------
# SAFETY DETECTION (Mode 2 QC)
# ---------------------------------------------------------

def ro_has_oil_service(ro_detail: Dict[str, Any]) -> bool:
    """
    Detect whether this RO involved an oil service.
    Uses a simple keyword scan across LaborList and Requests.
    """
    keywords = [
        "oil change",
        "lube, oil",
        "oil capacity verified",
        "oil filter",
        "synthetic oil",
        "engine oil",
    ]

    requests_text = (ro_detail.get("Requests") or "").lower()
    if any(k in requests_text for k in keywords):
        return True

    for labor in ro_detail.get("LaborList", []):
        desc = (labor.get("Description") or "").lower()
        if any(k in desc for k in keywords):
            return True

    # Also look for Oil Change Signoff checklist
    for cl in ro_detail.get("CheckLists", []):
        if "oil change signoff" in (cl.get("Name") or "").lower():
            return True

    return False


def ro_has_wheel_work(ro_detail: Dict[str, Any]) -> bool:
    """
    Detect whether any operation that requires wheel torque was done.
    """
    keywords = [
        "tire",
        "mount and balance",
        "wheel balance",
        "alignment",
        "brake",
        "cv axle",
        "strut",
        "shock",
        "control arm",
        "ball joint",
        "wheel bearing",
        "rim",
        "lug",
        "hub",
    ]

    requests_text = (ro_detail.get("Requests") or "").lower()
    if any(k in requests_text for k in keywords):
        return True

    for labor in ro_detail.get("LaborList", []):
        desc = (labor.get("Description") or "").lower()
        if any(k in desc for k in keywords):
            return True

    # Wheel Torque Signoff checklist
    for cl in ro_detail.get("CheckLists", []):
        if "wheel torque signoff" in (cl.get("Name") or "").lower():
            return True

    return False


# ---------------------------------------------------------
# PMA TECHNICIAN NOTES (for "NO" answers in QC)
# ---------------------------------------------------------

def save_pma_technician_notes(
    token: str,
    ro_number: str,
    pma_labor_id: str,
    notes: str,
) -> Dict[str, Any]:
    """
    Write notes to the PMA "Technician Notes" checklist item using:
    - Condition: "See Notes Below"
    - Comments: notes
    You already showed this item ID in your PMA JSON.
    """
    TECH_NOTES_ITEM_ID = "791b5ee9-3a37-4a09-b866-07cdf9412268"
    condition = "See Notes Below"
    return save_checklist(
        token=token,
        ro_number=ro_number,
        labor_id=pma_labor_id,
        item_id=TECH_NOTES_ITEM_ID,
        condition=condition,
        comments=notes,
    )
