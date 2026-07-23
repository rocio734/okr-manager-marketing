import requests
from datetime import datetime, timedelta

WINDSOR_API_KEY = __import__('os').environ["WINDSOR_API_KEY"]
BASE = "https://connectors.windsor.ai"

def test(label, connector, fields, start=30, end=0):
    t = datetime.today()
    df = (t - timedelta(days=start)).strftime("%Y-%m-%d")
    dt = (t - timedelta(days=end)).strftime("%Y-%m-%d")
    params = {"api_key": WINDSOR_API_KEY, "fields": ",".join(fields),
              "date_from": df, "date_to": dt}
    try:
        r = requests.get(f"{BASE}/{connector}", params=params, timeout=40)
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", data) if isinstance(data, dict) else data
        print(f"  ✅ {label}: {len(rows)} filas")
        for row in rows[:3]:
            print(f"     {row}")
    except Exception as e:
        print(f"  ❌ {label}: {e}")

print("=== Tests página-level Windsor.ai ===")
test("GA4: page_path + sessions",         "googleanalytics4", ["page_path","sessions","bounce_rate","average_session_duration"])
test("GA4: pagePath (camelCase)",          "googleanalytics4", ["pagePath","sessions"])
test("GA4: landingPage",                   "googleanalytics4", ["landingPage","sessions"])
test("GA4: page_title + sessions",         "googleanalytics4", ["page_title","sessions"])
test("SC: page + clicks + impressions",    "searchconsole",    ["page","clicks","impressions","ctr","position"])
