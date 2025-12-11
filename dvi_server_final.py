# dvi_server_final.py
#
# Single entrypoint Flask app for Railway.
# Uses your existing helper modules to:
#   - log into DVI
#   - change inspection status
#   - upload images to ROWriter BlobStorage
#   - attach images to checklist items
#   - save checklist text (comments + condition) via DVI JSON API
#
# Endpoints:
#   GET  /health
#   POST /dvi/start
#   POST /dvi/iso_inspection
#   POST /dvi/iso_complete
#   POST /dvi/pma_complete
#   POST /dvi/qc_complete
#   POST /dvi/upload_image   (simple temp-staging helper)

import os
import uuid
import base64
from pathlib import Path

from flask import Flask, request, jsonify
from flask_cors import CORS

# Your config – ISO_LABOR_ID and ISO_ITEM_ID are optional overrides
from config import ISO_LABOR_ID, ISO_ITEM_ID  # type: ignore[attr-defined]

from dvi_auth import dvi_login
from dvi_status import change_status
from dvi_media import save_media
from dvi_client import (
    get_ro_detail,
    get_checklist_items,
    find_labor_id_by_description,
    save_checklist as dvi_save_checklist,
    save_checklist_image_cloud as dvi_save_checklist_image_cloud,
)

app = Flask(__name__)
CORS(app)


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------
# Helpers to resolve ISO labor/item IDs dynamically
# ---------------------------------------------------------

def resolve_iso_labor_and_item(token: str, ro_number: str):
    """
    Dynamic lookup of the ISO labor_id and item_id for this RO.

    Priority:
      1) If ISO_LABOR_ID and ISO_ITEM_ID are set in config, use those.
      2) Use RO detail + checklist items to find the correct IDs:
         - Find labor whose Description contains 'ISO'
         - From its checklist items, prefer the one whose Title contains 'ISO',
           otherwise use the first item.
    """

    # 1) Static config path (quick path)
    try:
        static_labor = ISO_LABOR_ID
        static_item = ISO_ITEM_ID
    except NameError:  # if not defined in config
        static_labor = None
        static_item = None

    if static_labor and static_item:
        return static_labor, static_item

    # 2) Load RO detail once
    ro_detail = get_ro_detail(token, ro_number)

    # Find the ISO-like labor; adjust keyword if your shop uses different wording
    labor_id = find_labor_id_by_description(
        ro_detail,
        "ISO",  # or "Digital Multi-Point Vehicle Inspection"
    )
    if not labor_id:
        raise RuntimeError(f"Could not find ISO labor for RO {ro_number}")

    # 3) Pull all checklist items for that labor
    items_payload = get_checklist_items(token, labor_id)

    # get_checklist_items might return a list or a dict with 'Items'
    if isinstance(items_payload, list):
        items = items_payload
    else:
        items = items_payload.get("Items", [])

    if not items:
        raise RuntimeError(f"No checklist items returned for ISO labor {labor_id} / RO {ro_number}")

    # Prefer item whose title contains 'iso'; otherwise first
    iso_item = None
    for item in items:
        title = (item.get("Title") or "").lower()
        if "iso" in title:
            iso_item = item
            break

    if not iso_item:
        iso_item = items[0]

    item_id = iso_item.get("ID")
    if not item_id:
        raise RuntimeError(f"Could not determine ISO item ID for RO {ro_number}")

    return labor_id, item_id


# ---------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"ok": True, "message": "DVI bridge server running"})


# ---------------------------------------------------------
# START INSPECTION (status 3)
# ---------------------------------------------------------

@app.post("/dvi/start")
def dvi_start():
    data = request.get_json(force=True, silent=True) or {}
    ro_number = data.get("ro_number")

    if not ro_number:
        return jsonify({"ok": False, "error": "Missing required field 'ro_number'"}), 400

    try:
        token = dvi_login()
        raw = change_status(token, ro_number, "3")
        return jsonify({"ok": True, "ro_number": ro_number, "new_status": "3", "raw": raw})
    except Exception as e:
        log(f"/dvi/start error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------
# ISO INSPECTION
#
# Accepts:
#   - JSON: { ro_number, title?, comments?, condition?, image_paths?, images_base64? }
#   - multipart/form-data: fields + file uploads
#
# Behavior:
#   - Optionally move RO to status 3 (start)
#   - Upload all images to ROWriter BlobStorage
#   - Attach images to the ISO checklist item
#   - Save comments/condition to the ISO checklist item
#   - Optionally move RO to status 4 (ISO complete)
# ---------------------------------------------------------

@app.post("/dvi/iso_inspection")
def dvi_iso_inspection():
    # Accept JSON OR multipart/form-data
    if request.content_type and "multipart/form-data" in request.content_type:
        data = request.form.to_dict()
        files = request.files
    else:
        data = request.get_json(force=True, silent=True) or {}
        files = {}

    ro_number = data.get("ro_number")
    if not ro_number:
        return jsonify({"ok": False, "error": "Missing required field 'ro_number'"}), 400

    title = data.get("title", "ISO Vehicle Inspection")
    comments = data.get("comments", "")

    # Collect ALL images into local temp paths
    image_paths: list[str] = []

    # 1) Multipart files
    for _, f in files.items():
        if not f or not f.filename:
            continue
        tmp_path = Path(f"./tmp_{uuid.uuid4().hex}.jpg")
        f.save(tmp_path)
        image_paths.append(str(tmp_path))

    # 2) JSON "image_paths" (already on disk, e.g. from /dvi/upload_image)
    paths = data.get("image_paths", [])
    if isinstance(paths, list):
        image_paths.extend(paths)

    # 3) JSON "images_base64": either list of strings or list of dicts with {filename, data}
    images_b64 = data.get("images_base64", [])
    if isinstance(images_b64, list):
        for img in images_b64:
            try:
                if isinstance(img, str):
                    b64data = img
                    filename = f"img_{uuid.uuid4().hex}.jpg"
                else:
                    filename = img.get("filename", f"img_{uuid.uuid4().hex}.jpg")
                    b64data = img.get("data")
                if not b64data:
                    continue
                tmp_path = Path(f"./tmp_{uuid.uuid4().hex}_{filename}")
                with open(tmp_path, "wb") as f:
                    f.write(base64.b64decode(b64data))
                image_paths.append(str(tmp_path))
            except Exception as e:
                log(f"Base64 decode error: {e}")

    move_to_start = str(data.get("move_to_start", "true")).lower() == "true"
    move_to_complete = str(data.get("move_to_complete", "true")).lower() == "true"

    log(f"/dvi/iso_inspection ro={ro_number}, title={title}, images={len(image_paths)}")

    blobs = []
    upload_errors = []
    status_changes = {}
    tmp_files_cleanup = list(image_paths)

    try:
        token = dvi_login()

        # Dynamically resolve ISO labor + item IDs (falls back to config if set)
        labor_id, item_id = resolve_iso_labor_and_item(token, ro_number)
        log(f"Resolved ISO labor_id={labor_id}, item_id={item_id} for RO {ro_number}")

        # 1) Upload images to ROWriter BlobStorage and attach to ISO checklist
        for p in image_paths:
            try:
                blob = save_media(token, image_path=p)
                dvi_save_checklist_image_cloud(
                    token=token,
                    ro_number=ro_number,
                    labor_id=labor_id,
                    item_id=item_id,
                    blob_name=blob,
                )
                blobs.append(blob)
            except Exception as e:
                msg = f"Failed to upload/attach {p}: {e}"
                log(msg)
                upload_errors.append(msg)

        # 2) Save comments (and optionally condition) via DVI JSON API SaveChecklist
        condition = data.get("condition", "") or ("Failed Inspection" if comments else "")
        checklist_result = dvi_save_checklist(
            token=token,
            ro_number=ro_number,
            labor_id=labor_id,
            item_id=item_id,
            condition=condition,
            comments=comments,
            ro_type="R",
        )

        # 3) Status moves
        if move_to_start:
            status_changes["start"] = change_status(token, ro_number, "3")
        if move_to_complete:
            status_changes["iso_complete"] = change_status(token, ro_number, "4")

        return jsonify(
            {
                "ok": True,
                "ro_number": ro_number,
                "title": title,
                "labor_id": labor_id,
                "item_id": item_id,
                "blobs": blobs,
                "upload_errors": upload_errors,
                "checklist": checklist_result,
                "status_changes": status_changes,
            }
        )

    except Exception as e:
        log(f"/dvi/iso_inspection error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        # Clean up any temp files we created
        for p in tmp_files_cleanup:
            try:
                fp = Path(p)
                if fp.exists() and fp.is_file() and fp.name.startswith("tmp_"):
                    fp.unlink()
            except Exception:
                pass


# ---------------------------------------------------------
# SIMPLE IMAGE STAGING ENDPOINT
#
# Lets a client upload one file and get a temp path back,
# which can then be passed into /dvi/iso_inspection.image_paths.
# ---------------------------------------------------------

@app.post("/dvi/upload_image")
def dvi_upload_image():
    try:
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file part"}), 400

        file = request.files["file"]
        if not file or not file.filename:
            return jsonify({"ok": False, "error": "Empty filename"}), 400

        tmp_path = Path(f"./tmp_{uuid.uuid4().hex}.jpg")
        file.save(tmp_path)

        return jsonify({"ok": True, "temp_path": str(tmp_path)})
    except Exception as e:
        log(f"/dvi/upload_image error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------
# COMPLETE ISO / PMA / QC (status-only helpers)
# These don’t touch text or images, just call ChangeStatus.
# ---------------------------------------------------------

@app.post("/dvi/iso_complete")
def dvi_iso_complete():
    data = request.get_json(force=True, silent=True) or {}
    ro_number = data.get("ro_number")
    if not ro_number:
        return jsonify({"ok": False, "error": "Missing ro_number"}), 400

    try:
        token = dvi_login()
        raw = change_status(token, ro_number, "4")
        return jsonify({"ok": True, "ro_number": ro_number, "new_status": "4", "raw": raw})
    except Exception as e:
        log(f"/dvi/iso_complete error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/dvi/pma_complete")
def dvi_pma_complete():
    data = request.get_json(force=True, silent=True) or {}
    ro_number = data.get("ro_number")
    if not ro_number:
        return jsonify({"ok": False, "error": "Missing ro_number"}), 400

    try:
        token = dvi_login()
        # adjust status code if your environment uses a different one for PMA complete
        raw = change_status(token, ro_number, "5")
        return jsonify({"ok": True, "ro_number": ro_number, "new_status": "5", "raw": raw})
    except Exception as e:
        log(f"/dvi/pma_complete error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/dvi/qc_complete")
def dvi_qc_complete():
    data = request.get_json(force=True, silent=True) or {}
    ro_number = data.get("ro_number")
    if not ro_number:
        return jsonify({"ok": False, "error": "Missing ro_number"}), 400

    try:
        token = dvi_login()
        # adjust status code if your environment uses a different one for QC
        raw = change_status(token, ro_number, "8")
        return jsonify({"ok": True, "ro_number": ro_number, "new_status": "8", "raw": raw})
    except Exception as e:
        log(f"/dvi/qc_complete error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------
# MAIN (for local dev and Railway Procfile)
# ---------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5003))
    log(f"Starting DVI server on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
