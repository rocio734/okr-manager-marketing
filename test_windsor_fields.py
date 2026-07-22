import requests
from datetime import datetime, timedelta

WINDSOR_API_KEY = __import__('os').environ["WINDSOR_API_KEY"]
BASE = "https://connectors.windsor.ai"

def test(label, fields, start=30, end=0):
    t = datetime.today()
    df = (t - timedelta(days=start)).strftime("%Y-%m-%d")
    dt = (t - timedelta(days=end)).strftime("%Y-%m-%d")
    params = {"api_key": WINDSOR_API_KEY, "fields": ",".join(fields),
              "date_from": df, "date_to": dt}
    try:
        r = requests.get(f"{BASE}/googleanalytics4", params=params, timeout=40)
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", data) if isinstance(data, dict) else data
        total = sum(float(row.get(fields[-1]) or 0) for row in rows)
        print(f"  ✅ {label}: OK — {len(rows)} filas, suma({fields[-1]})={total:.0f}")
    except Exception as e:
        print(f"  ❌ {label}: {e}")

print("=== Tests Windsor.ai GA4 ===")
test("Test 1 — solo new_users",                ["new_users"])
test("Test 2 — sessions + new_users",           ["sessions", "new_users"])
test("Test 3 — newUsers camelCase",             ["newUsers"])
test("Test 4 — combo sin new_users (baseline)", ["sessions","active_users","screen_page_views","engagement_rate","bounce_rate","average_session_duration"])
test("Test 5 — active_users solo",              ["active_users"])
test("Test 6 — new_users + date dim",           ["date", "new_users"])
