import os, json, urllib.request, urllib.parse

BASE  = os.environ["ETENDO_BASE_URL"]
USER  = os.environ["ETENDO_USERNAME"]
PASS  = os.environ["ETENDO_PASSWORD"]
ROLE  = "8351131DFF384725AB08E06773FE6144"

def login():
    body = json.dumps({"username": USER, "password": PASS, "role": ROLE}).encode()
    req  = urllib.request.Request(f"{BASE}/api/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["token"]

def fetch_page(token, start=0):
    body = urllib.parse.urlencode({
        "_operationType": "fetch", "_startRow": str(start), "_endRow": str(start + 10),
    }).encode()
    req = urllib.request.Request(f"{BASE}/api/datasource/ETCRM_Lead", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("response", {}).get("data", [])

print("=== CRM field discovery ===")
token = login()
print("✅ Login OK")
leads = fetch_page(token)
print(f"✅ {len(leads)} leads (primera página)")
if leads:
    print("\nCampos disponibles en el primer lead:")
    for k, v in leads[0].items():
        print(f"  {k}: {repr(v)[:80]}")
    print("\nStatus/clasificación de los 10 leads:")
    for l in leads:
        print(f"  status={l.get('leadStatus$_identifier')} | class={l.get('classification$_identifier')} | name={l.get('name')} | bp={l.get('businessPartner$_identifier')} | country={l.get('country$_identifier') or l.get('countryId$_identifier')}")
