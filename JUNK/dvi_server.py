# ---------------------------------------------------------
# DVI SERVER WITH BASE64 IMAGE SUPPORT ONLY
# (No other behavior changed)
# ---------------------------------------------------------

import base64
import uuid
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

from dvi_auth import dvi_login
from dvi_checklist import (
    save_checklist,
    save_checklist_image_cloud,
    lookup_ids_for_ro_and_title
)

from dvi_status import change_status
from dvi_media import save_media
from config import (
    ISO_LABOR_ID,
    ISO_ITEM_ID,
)

app = Flask(__name__)
CORS(app)


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({"ok": True, "message": "DVI server running"})


# ---------------------------------------------------------
# START INSPECTION
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
# ISO INSPECTION (Supports JSON, multipart, AND base64)
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

    # Required fields
    ro_number = data.get("ro_number")
    if not ro_number:
        return jsonify({"ok": False, "error": "Missing required field 'ro_number'"}), 400

    title = data.get("title", "ISO")
    comments = data.get("comments", "")

    # Collect ALL images here
    image_paths = []

    # 1) Multipart files
    for _, f in files.items():
        tmp_path = Path(f"./tmp_{uuid.uuid4().hex}.jpg")
        f.save(tmp_path)
        image_paths.append(str(tmp_path))

    # 2) JSON image paths (existing behavior)
    if "image_paths" in data:
        paths = data.get("image_paths", [])
        if isinstance(paths, list):
            image_paths.extend(paths)

    # ---------------------------------------------------------
    # 3) NEW FEATURE — BASE64 image uploads
    # ---------------------------------------------------------
    if "images_base64" in data:
        try:
            for img in data["images_base64"]:
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

    try:
        token = dvi_login()

        labor_id = ISO_LABOR_ID
        item_id = ISO_ITEM_ID

        blobs = []
        upload_errors = []

        # Upload each image
        for p in image_paths:
            try:
                blob = save_media(token, p)
                save_checklist_image_cloud(token, ro_number, labor_id, item_id, blob)
                blobs.append(blob)
            except Exception as e:
                msg = f"Failed to upload {p}: {e}"
                log(msg)
                upload_errors.append(msg)

        # Save comments
        checklist = save_checklist(
            token, ro_number, labor_id, item_id, title, comments
        )

        # Status moves
        status_changes = {}
        if move_to_start:
            status_changes["start"] = change_status(token, ro_number, "3")
        if move_to_complete:
            status_changes["iso_complete"] = change_status(token, ro_number, "4")

        return jsonify({
            "ok": True,
            "ro_number": ro_number,
            "title": title,
            "blobs": blobs,
            "upload_errors": upload_errors,
            "checklist": checklist,
            "status_changes": status_changes
        })

    except Exception as e:
        log(f"/dvi/iso_inspection error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# =========================================================
# PMA INSPECTION (same structure as ISO)
# =========================================================

PMA_LABOR_ID = "PUT-YOUR-PMA-LABOR-ID-HERE"
PMA_ITEM_ID = "PUT-YOUR-PMA-ITEM-ID-HERE"

@app.post("/dvi/pma_inspection")
def dvi_pma_inspection():

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

    title = data.get("title", "PMA Inspection")
    comments = data.get("comments", "")

    # Build image list
    image_paths = []

    # Multipart images
    for _, f in files.items():
        tmp_path = Path(f"./tmp_{uuid.uuid4().hex}.jpg")
        f.save(tmp_path)
        image_paths.append(str(tmp_path))

    # Base64 images
    base64_list = data.get("images_base64", [])
    for b64 in base64_list:
        try:
            raw = base64.b64decode(b64)
            tmp_path = Path(f"./tmp_{uuid.uuid4().hex}.jpg")
            with open(tmp_path, "wb") as f:
                f.write(raw)
            image_paths.append(str(tmp_path))
        except Exception as e:
            log(f"Base64 decode failed: {e}")

    move_to_start = data.get("move_to_start", "false").lower() == "true"
    move_to_complete = data.get("move_to_complete", "true").lower() == "true"

    log(f"/dvi/pma_inspection ro={ro_number}, images={len(image_paths)}")

    try:
        token = dvi_login()

        blobs = []
        upload_errors = []

        for p in image_paths:
            try:
                blob = save_media(token, p)
                save_checklist_image_cloud(token, ro_number, PMA_LABOR_ID, PMA_ITEM_ID, blob)
                blobs.append(blob)
            except Exception as e:
                msg = f"Failed to upload {p}: {e}"
                log(msg)
                upload_errors.append(msg)

        checklist = save_checklist(
            token, ro_number, PMA_LABOR_ID, PMA_ITEM_ID, title, comments
        )

        status_changes = {}
        if move_to_start:
            status_changes["start"] = change_status(token, ro_number, "3")
        if move_to_complete:
            status_changes["pma_complete"] = change_status(token, ro_number, "4")

        return jsonify({
            "ok": True,
            "ro_number": ro_number,
            "title": title,
            "blobs": blobs,
            "upload_errors": upload_errors,
            "checklist": checklist,
            "status_changes": status_changes
        })

    except Exception as e:
        log(f"/dvi/pma_inspection error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# =========================================================
# MARK PMA COMPLETE ONLY (simple endpoint)
# =========================================================

@app.post("/dvi/pma_complete")
def dvi_pma_complete():
    data = request.get_json(force=True, silent=True) or {}
    ro_number = data.get("ro_number")

    if not ro_number:
        return jsonify({"ok": False, "error": "Missing required field 'ro_number'"}), 400

    try:
        token = dvi_login()
        status_raw = change_status(token, ro_number, "4")

        return jsonify({
            "ok": True,
            "ro_number": ro_number,
            "status": "PMA complete",
            "raw": status_raw
        })

    except Exception as e:
        log(f"/dvi/pma_complete error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500



# ---------------------------------------------------------
# PMA COMPLETE (UNCHANGED)
# ---------------------------------------------------------
@app.post("/dvi/pma_complete")
def dvi_pma_complete():
    data = request.get_json(force=True, silent=True) or {}
    ro_number = data.get("ro_number")

    if not ro_number:
        return jsonify({"ok": False, "error": "Missing required field 'ro_number'"}), 400

    try:
        token = dvi_login()
        raw = change_status(token, ro_number, "5")  # PMA status
        return jsonify({"ok": True, "ro_number": ro_number, "new_status": "5", "raw": raw})
    except Exception as e:
        log(f"/dvi/pma_complete error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
if __name__ == "__main__":
    print("Starting DVI server on 0.0.0.0:5003")
    app.run(host="0.0.0.0", port=5003, debug=True)
