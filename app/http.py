import json, urllib.request, urllib.parse, urllib.error

class HttpError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:300]}"); self.status, self.body = status, body

def request(method, url, params=None, data=None, headers=None, json_body=None, timeout=60, raw=False):
    if params: url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    h = dict(headers or {}); body = None
    if json_body is not None:
        body = json.dumps(json_body).encode(); h["Content-Type"] = "application/json"
    elif data is not None:
        body = data if isinstance(data, bytes) else urllib.parse.urlencode(data).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, body, h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            if raw: return r.status, dict(r.headers), payload
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"))
