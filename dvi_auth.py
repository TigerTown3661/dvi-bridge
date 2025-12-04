# dvi_auth.py
import requests

# -------------------------
# CONFIG – EDIT THESE
# -------------------------
DVI_BASE = "https://dviapi.rowriter.com"
DVI_USERNAME = "hometownmidas+21@gmail.com"
DVI_PASSWORD = "Password!"
CIM_CODE = "80746"

def dvi_login() -> str:
    """
    Authenticate with the DVI API and return a bearer token.
    """
    url = f"{DVI_BASE}/login"
    body = {
        "DataServer": "20",
        "UserName": DVI_USERNAME,
        "Password": DVI_PASSWORD,
        "TouchVersion": "Touch for iOS",
        "PushID": "PythonBridge"
    }
    headers = {
        "Content-Type": "application/json",
        "cim": CIM_CODE
    }

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data["Token"]
