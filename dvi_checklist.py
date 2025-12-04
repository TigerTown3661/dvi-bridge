import requests

DVI_BASE = "https://dviapi.rowriter.com"


# ---------------------------------------------------------
# SaveChecklistImageCloud
# ---------------------------------------------------------
def save_checklist_image_cloud(
    token: str,
    ro_number: str,
    labor_id: str,
    item_id: str,
    blob_name: str,
    ro_type: str = "R"
):
    """
    Attaches an uploaded media blob to a checklist item.

    Required fields:
      - RONumber
      - LaborID (ISO usually passes "")
      - ItemID  (ISO uses the ISO-checklist GUID or item GUID)
      - ROType
      - Media [ blob name returned from SaveMedia ]
    """

    url = f"{DVI_BASE}/SaveChecklistImageCloud"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "RONumber": ro_number,
        "LaborID": labor_id or "",
        "ItemID": item_id,
        "ROType": ro_type,
        "Media": [blob_name],
        "ImageName": "",
        "Description": "",
    }

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------
# SaveChecklist (generic item update)
# ---------------------------------------------------------
def save_checklist(
    token: str,
    ro_number: str,
    labor_id: str,
    item_id: str,
    title: str,
    comments: str,
    condition: str,
    ro_type: str = "R"
):
    """
    Updates a standard non-ISO checklist item.
    """
    url = f"{DVI_BASE}/SaveChecklist"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "RONumber": ro_number,
        "LaborID": labor_id,
        "ItemID": item_id,
        "Title": title,
        "Comments": comments,
        "Condition": condition,
        "ROType": ro_type,
    }

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------
# SaveChecklistByChecklistID
# ---------------------------------------------------------
def save_checklist_by_checklist_id(
    token: str,
    ro_number: str,
    checklist_id: str,
    item_id: str,
    condition: str,
    comments: str,
    ro_type: str = "R"
):
    """
    Updates an item using a ChecklistID instead of LaborID.
    """
    url = f"{DVI_BASE}/SaveChecklistByChecklistID"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "RONumber": ro_number,
        "ChecklistID": checklist_id,
        "ItemID": item_id,
        "Comments": comments,
        "Condition": condition,
        "ROType": ro_type,
    }

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------
# Optional: Prime ISO comment field
# ---------------------------------------------------------
def prime_iso_comment_field(token: str, ro_number: str, iso_checklist_id: str):
    """
    Creates an empty ISO comment entry so the UI has a line to attach content to.
    """
    url = f"{DVI_BASE}/SaveChecklistByChecklistID"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "RONumber": ro_number,
        "ChecklistID": iso_checklist_id,
        "ItemID": iso_checklist_id,      # ISO treats ID as self-item
        "Comments": "",
        "Condition": "",
        "ROType": "R",
    }

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text
