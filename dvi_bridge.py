import os
import uuid
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_file

from dvi_checklist import (
    save_checklist_by_checklist_id,
    save_checklist_image_cloud,
    save_checklist,
    # make sure this exists in dvi_checklist.py
    # or remove it from the /dvi/prime_iso route below
    prime_iso_comment_field,
)

# --------------------------------------------------------
# CONFIGURATION
# --------------------------------------------------------
DVI_BASE = "https://dviapi.rowriter.com"
ROW_API_BASE = "https://api.rowriter.com"

DVI_USERNAME = "hometownmidas+21@gmail.com"
DVI_PASSWORD = "Password!"
CIM_CODE = "80746"  # store CIM

# Fallback ISO checklist ID (used if we can't auto-detect)
ISO_CHECKLIST_ID = "7dfec4da-e129-4bff-860f-f3f1c440708b"

app = Flask(__name__)


# --------------------------------------------------------
# LOGIN → get Bearer token
# --------------------------------------------------------
def dvi_login() -> str:
    url = f"{DVI_BASE}/login"
    body = {
        "DataServer": "20",
        "UserName": DVI_USERNAME,
        "Password": DVI_PASSWORD,
        "TouchVersion": "Touch for iOS",
        "PushID": "PythonBridge",
    }
    headers = {
        "Content-Type": "application/json",
        "cim": CIM_CODE,
    }
    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data["Token"]


# --------------------------------------------------------
# Upload image to ROWriter Media Storage
# --------------------------------------------------------
def save_media(token: str, image_path: str) -> str:
    """
    Uploads a file to ROWriter BlobStorage and returns the blob name.
    """
    blob_name = f"{uuid.uuid4().hex}.jpg"

    url = f"{ROW_API_BASE}/v2/BlobStorage/SaveMedia"
    headers = {"Authorization": f"Bearer {token}"}
    options_json = '{"Tier":0,"Location":0,"test":null}'

    with open(image_path, "rb") as f:
        files = {
            "Options": (None, options_json, "application/json"),
            blob_name: (blob_name, f, "image/jpeg"),
        }

        resp = requests.post(url, headers=headers, files=files, timeout=60)
        resp.raise_for_status()

    # API returns a quoted string with the blob name
    return resp.json().strip('"')


# --------------------------------------------------------
# Change inspection status
# --------------------------------------------------------
def change_status(token: str, ro_number: str, status: str, ro_type: str = "R") -> str:
    """
    Status codes (as currently used):
      3 = In Progress / Start
      4 = Complete (ISO)
      5 = Complete (PMA)
      8 = QC Complete
    """
    url = f"{DVI_BASE}/ChangeStatus"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "RONumber": ro_number,
        "Status": status,
        "Type": ro_type,
    }

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


# --------------------------------------------------------
# Helper: normalize boolean from form-data / JSON
# --------------------------------------------------------
def _form_bool(val, default: bool = True) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


# --------------------------------------------------------
# Helper: robust RowID lookup from HTML
# --------------------------------------------------------
def get_rowid_for_ro(token: str, ro_number: str) -> str:
    """
    Tries to derive the DVI RowID (GUID) for a given RO number by:
      1) Requesting Checklist.aspx with the RONumber if supported
      2) Falling back to EditChecklist.aspx with the RONumber
      3) Parsing either:
         - the <form action="...rowid=<guid>&Type=R">, or
         - the hidden input hOriginalROWID
    """
    headers = {"Authorization": f"Bearer {token}"}
    last_error = None

    # Try in this order so we can adapt to whichever one works in your environment
    candidates = [
        f"{DVI_BASE}/Checklist.aspx?Type=R&RONumber={ro_number}",
        f"{DVI_BASE}/EditChecklist.aspx?Type=R&RONumber={ro_number}",
    ]

    for url in candidates:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            # If this URL is not valid at all it'll 404; move on
            if resp.status_code >= 400:
                last_error = f"{url} → {resp.status_code}"
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # 1) Preferred: parse RowID from form action
            form = soup.find("form")
            if form:
                action = form.get("action", "")
                # e.g. "./Checklist.aspx?ID=6894...&Type=R"
                if "ID=" in action:
                    rowid = action.split("ID=")[1].split("&")[0]
                    if rowid:
                        return rowid

                # e.g. "./EditChecklist.aspx?rowid=cd64...&Type=R"
                if "rowid=" in action:
                    rowid = action.split("rowid=")[1].split("&")[0]
                    if rowid:
                        return rowid

            # 2) Fallback: some pages include a hidden field hOriginalROWID
            hidden_rowid = soup.find("input", {"id": "hOriginalROWID"})
            if hidden_rowid and hidden_rowid.get("value"):
                return hidden_rowid["value"]

            last_error = f"No RowID found in action or hOriginalROWID for URL: {url}"

        except Exception as e:
            last_error = f"{url} → {type(e).__name__}: {e}"

    raise RuntimeError(
        f"Unable to determine RowID for RONumber {ro_number}. Last error: {last_error}"
    )


# --------------------------------------------------------
# REQUIRED: Correct WebForm submission for ISO comments
# --------------------------------------------------------
def _post_iso_webform_comment(token: str, rowid: str, comment_text: str, condition: str = "") -> bool:
    """
    Posts ISO comments using the actual ROWriter WebForm.
    This is what makes notes appear in the native DVI UI.

    Note:
      The exact control IDs (ctl07, ctl09, etc.) may differ between installs.
      This version:
        - grabs __VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION, hOriginalROWID
        - tries to locate the comment + condition fields in a robust way
        - falls back to ctl07 / ctl09 if necessary
    """
    # 1️⃣ GET the WebForm for this rowid
    url = f"{DVI_BASE}/EditChecklist.aspx?rowid={rowid}&Type=R"
    headers = {"Authorization": f"Bearer {token}"}
    get_resp = requests.get(url, headers=headers, timeout=15)
    get_resp.raise_for_status()

    soup = BeautifulSoup(get_resp.text, "html.parser")

    def _get_value(input_id: str, required: bool = True) -> str:
        tag = soup.find("input", {"id": input_id})
        if not tag or "value" not in tag.attrs:
            if required:
                raise RuntimeError(f"Missing required hidden field: {input_id}")
            return ""
        return tag["value"]

    viewstate = _get_value("__VIEWSTATE")
    viewstate_gen = _get_value("__VIEWSTATEGENERATOR")
    event_validation = _get_value("__EVENTVALIDATION")
    original_rowid = _get_value("hOriginalROWID")

    # Try to infer comment + condition fields
    # Common pattern is:
    #   ctl07 = condition
    #   ctl09 = comment
    # but we try to be defensive.
    comment_field_name = None
    condition_field_name = None

    # Heuristic: look for textarea closest to a label mentioning "ISO" or "Inspection"
    textareas = soup.find_all("textarea")
    if textareas:
        # just pick the last textarea as comment if we don't find anything smarter
        comment_field_name = textareas[-1].get("name")

    # look for any select or input that might be a dropdown for condition
    selects = soup.find_all("select")
    if selects:
        condition_field_name = selects[0].get("name")

    # Hard-coded fallbacks (known working in your earlier scripts)
    if not condition_field_name:
        condition_field_name = "ctl07"
    if not comment_field_name:
        comment_field_name = "ctl09"

    # 2️⃣ POST back the WebForm as ROWriter does
    payload = {
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": viewstate_gen,
        "__EVENTVALIDATION": event_validation,
        "hOriginalROWID": original_rowid,
        comment_field_name: comment_text or "",
        condition_field_name: condition or "",
        "bSave": "Save",
    }

    post_resp = requests.post(url, headers=headers, data=payload, timeout=15)
    post_resp.raise_for_status()

    return True


# --------------------------------------------------------
# ROUTES: Start / Complete statuses
# --------------------------------------------------------
@app.post("/dvi/start")
def dvi_start():
    try:
        data = request.get_json(force=True)
        ro_number = data["ro_number"]

        token = dvi_login()
        result = change_status(token, ro_number, "3")

        return jsonify(
            {
                "ok": True,
                "new_status": "3",
                "ro_number": ro_number,
                "raw": result,
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "error": str(e), "type": type(e).__name__}),
            500,
        )


@app.post("/dvi/iso_complete")
def dvi_iso_complete():
    try:
        data = request.get_json(force=True)
        ro_number = data["ro_number"]

        token = dvi_login()
        result = change_status(token, ro_number, "4")

        return jsonify(
            {
                "ok": True,
                "new_status": "4",
                "ro_number": ro_number,
                "raw": result,
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "error": str(e), "type": type(e).__name__}),
            500,
        )


@app.post("/dvi/pma_complete")
def dvi_pma_complete():
    try:
        data = request.get_json(force=True)
        ro_number = data["ro_number"]

        token = dvi_login()
        result = change_status(token, ro_number, "5")

        return jsonify(
            {
                "ok": True,
                "new_status": "5",
                "ro_number": ro_number,
                "raw": result,
            }
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "error": str(e), "type": type(e).__name__}),
            500,
        )


# --------------------------------------------------------
# ROUTE: ISO INSPECTION (main)
# --------------------------------------------------------
@app.post("/dvi/iso_inspection")
def dvi_iso_inspection():
    temp_files = []
    try:
        # ------------------------------------------------
        # 1) Parse input: support multipart (images) or JSON
        # ------------------------------------------------
        if request.content_type and request.content_type.startswith(
            "multipart/form-data"
        ):
            form = request.form
            ro_number = form.get("ro_number")
            comments = form.get("comments", "")
            rowid = form.get("rowid")  # optional

            move_to_start = _form_bool(form.get("move_to_start"), True)
            move_to_complete = _form_bool(form.get("move_to_complete"), True)

            # Save uploaded images to /tmp
            image_files = request.files.getlist("images")
            image_paths = []
            for file in image_files:
                if not file or not file.filename:
                    continue
                temp_path = f"/tmp/{uuid.uuid4().hex}.jpg"
                file.save(temp_path)
                temp_files.append(temp_path)
                image_paths.append(temp_path)
        else:
            data = request.get_json(force=True)
            ro_number = data.get("ro_number")
            comments = data.get("comments", "")
            rowid = data.get("rowid")  # optional

            move_to_start = bool(data.get("move_to_start", True))
            move_to_complete = bool(data.get("move_to_complete", True))

            # JSON variant expects image_paths already on disk
            image_paths = data.get("image_paths", [])

        if not ro_number:
            return jsonify({"ok": False, "error": "Missing ro_number"}), 400

        # ------------------------------------------------
        # 2) Get token & RowID (auto-detect if needed)
        # ------------------------------------------------
        token = dvi_login()

        if not rowid:
            rowid = get_rowid_for_ro(token, ro_number)

        status_changes = {}

        # ------------------------------------------------
        # 3) Move ISO to "start" (status 3)
        # ------------------------------------------------
        if move_to_start:
            status_changes["start"] = change_status(token, ro_number, "3")

        # ------------------------------------------------
        # 4) Save ISO comment via WebForm (this is the fix)
        # ------------------------------------------------
        _post_iso_webform_comment(
            token=token,
            rowid=rowid,
            comment_text=comments,
            condition="Failed Inspection" if comments else "",
        )

        # ------------------------------------------------
        # 5) Attach images to ISO checklist
        # ------------------------------------------------
        blobs = []
        for path in image_paths:
            if not path:
                continue
            try:
                blob = save_media(token, path)
                # NOTE: this relies on your existing dvi_checklist.save_checklist_image_cloud
                # signature: (token, ro_number, labor_id, item_id, blob_name, ro_type="R")
                # For ISO we typically don't have a labor_id, so we pass "".
                save_checklist_image_cloud(
                    token,
                    ro_number,
                    "",
                    ISO_CHECKLIST_ID,
                    blob,
                )
                blobs.append(blob)
            except Exception as e:
                print("IMAGE ATTACH ERROR:", e)

        # ------------------------------------------------
        # 6) Move ISO to "complete" (status 4)
        # ------------------------------------------------
        if move_to_complete:
            status_changes["complete"] = change_status(token, ro_number, "4")

        return jsonify(
            {
                "ok": True,
                "ro_number": ro_number,
                "rowid": rowid,
                "comments_saved": comments,
                "blobs": blobs,
                "checklist_id": ISO_CHECKLIST_ID,
                "status_changes": status_changes,
            }
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "error": str(e), "type": type(e).__name__}),
            500,
        )
    finally:
        # clean up temp files from multipart uploads
        for p in temp_files:
            try:
                if p.startswith("/tmp/") and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# --------------------------------------------------------
# Other existing routes (upload_image, PMA notes, QC, prime ISO)
# --------------------------------------------------------
@app.post("/dvi/upload_image")
def dvi_upload_image():
    """
    Simple helper that saves a single uploaded file to /tmp and returns the temp path.
    Used as a staging step so the caller can then tell /dvi/iso_inspection
    which temp paths to use in image_paths.
    """
    try:
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file part"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"ok": False, "error": "Empty filename"}), 400

        temp_path = f"/tmp/{uuid.uuid4().hex}.jpg"
        file.save(temp_path)

        return jsonify({"ok": True, "temp_path": temp_path})
    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "error": str(e), "type": type(e).__name__}),
            500,
        )


@app.post("/dvi/pma_technician_notes")
def dvi_pma_technician_notes():
    """
    Save PMA technician notes into a specific checklist item.
    """
    try:
        data = request.get_json(force=True)

        ro_number = data.get("ro_number")
        labor_id = data.get("labor_id")
        notes = data.get("notes")

        if not ro_number or not labor_id or not notes:
            return (
                jsonify({"ok": False, "error": "Missing required fields"}),
                400,
            )

        token = dvi_login()
        TECH_NOTES_ITEM_ID = "791b5ee9-3a37-4a09-b866-07cdf9412268"

        result = save_checklist(
            token=token,
            ro_number=ro_number,
            labor_id=labor_id,
            item_id=TECH_NOTES_ITEM_ID,
            title="Technician Notes",
            comments=notes,
            condition="See Notes Below",
        )

        return jsonify({"ok": True, "result": result})

    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "error": str(e), "type": type(e).__name__}),
            500,
        )


@app.post("/dvi/qc_complete")
def dvi_qc_complete():
    """
    Mark QC as complete (status 8) for a given RO.
    """
    try:
        data = request.get_json(force=True)
        ro_number = data.get("ro_number")

        if not ro_number:
            return jsonify({"ok": False, "error": "Missing ro_number"}), 400

        token = dvi_login()
        result = change_status(token, ro_number, "8")

        return jsonify(
            {
                "ok": True,
                "new_status": "8",
                "ro_number": ro_number,
                "raw": result,
            }
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "error": str(e), "type": type(e).__name__}),
            500,
        )


@app.post("/dvi/prime_iso")
def dvi_prime_iso():
    """
    Optional helper to initialize / prime ISO comment field using your
    dvi_checklist.prime_iso_comment_field implementation.
    """
    try:
        data = request.get_json(force=True)
        ro_number = data["ro_number"]

        token = dvi_login()
        prime_iso_comment_field(token, ro_number, ISO_CHECKLIST_ID)

        return jsonify(
            {
                "ok": True,
                "ro_number": ro_number,
                "message": "ISO comment field primed and ready.",
            }
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify({"ok": False, "error": str(e), "type": type(e).__name__}),
            500,
        )


@app.route("/openapi.yaml")
def serve_openapi():
    return send_file("openapi.yaml", mimetype="text/yaml")


# --------------------------------------------------------
# ROUTE: Get RowID for ISO inspection (HTML scraper version)
# --------------------------------------------------------
@app.get("/dvi/get_rowid")
def dvi_get_rowid():
    try:
        ro_number = request.args.get("ro_number")
        if not ro_number:
            return jsonify({"ok": False, "error": "Missing ro_number"}), 400

        token = dvi_login()

        # THIS IS THE FIX: use dvi.rowriter.com (not dviapi.rowriter.com)
        url = f"https://dvi.rowriter.com/EditChecklist.aspx?Type=R&RONumber={ro_number}"

        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code != 200:
            return jsonify({
                "ok": False,
                "error": f"Checklist page returned HTTP {resp.status_code}",
                "url": url
            }), 500

        soup = BeautifulSoup(resp.text, "html.parser")

        # Grab rowid from:
        # <input type="hidden" id="hOriginalROWID" value="GUID" />
        rowid_tag = soup.find("input", {"id": "hOriginalROWID"})
        if not rowid_tag:
            return jsonify({
                "ok": False,
                "error": "RowID not found in returned HTML",
                "url": url
            }), 500

        rowid = rowid_tag.get("value")

        return jsonify({
            "ok": True,
            "ro_number": ro_number,
            "rowid": rowid
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500





# --------------------------------------------------------
# RUN LOCAL
# --------------------------------------------------------
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8010))
    app.run(host="0.0.0.0", port=port)

