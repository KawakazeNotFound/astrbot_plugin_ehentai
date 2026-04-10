import httpx
import json

resp = httpx.post(
    "https://eh.shirasuazusa.workers.dev/",
    json={"keyword": "english", "debug": True}
)
try:
    print(json.dumps(resp.json(), indent=2))
except:
    print(resp.text)
