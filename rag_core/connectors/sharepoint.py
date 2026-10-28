"""SharePoint (Microsoft Graph drive delta) as a source: a local mirror kept up to date by delta (step 5-3)."""
from __future__ import annotations

import json
import os
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from rag_core import source_registry as registry
from rag_core.source_registry import SourceError
from rag_core.source_scan import max_file_bytes, supported_extensions

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"  # nosec B105 - an endpoint, not a credential
SCOPE = "https://graph.microsoft.com/.default"
MAX_WAIT_SECONDS = 60
RETRYABLE = {429, 503, 504}

Transport = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, bytes, Dict[str, str]]]


class GraphError(RuntimeError):
    """A failed Graph call. The message names the status, never a token, a secret or a URL with credentials."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class ResyncRequired(GraphError):
    """HTTP 410: the delta link is no longer valid; start again from a full listing."""


def urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes] = None) -> Tuple[int, bytes, Dict[str, str]]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310  # nosec B310 - https URLs of the Graph API / its download links
            return response.status, response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b"", {k.lower(): v for k, v in (exc.headers or {}).items()}
    except urllib.error.URLError as exc:
        raise GraphError(f"network error: {type(exc.reason).__name__}") from None


def token_provider(env: Optional[Dict[str, str]] = None, transport: Transport = urllib_transport,
                   clock: Callable[[], float] = time.time) -> Callable[[], str]:
    """A function returning a bearer token: the fixed SHAREPOINT_ACCESS_TOKEN, or one requested with the client credentials."""
    env = os.environ if env is None else env
    fixed = str(env.get("SHAREPOINT_ACCESS_TOKEN") or "").strip()
    if fixed:
        return lambda: fixed
    tenant, client_id, secret = (str(env.get(k) or "").strip() for k in
                                 ("SHAREPOINT_TENANT_ID", "SHAREPOINT_CLIENT_ID", "SHAREPOINT_CLIENT_SECRET"))
    if not (tenant and client_id and secret):
        raise SourceError("SharePoint access is not configured (SHAREPOINT_ACCESS_TOKEN, or SHAREPOINT_TENANT_ID / "
                          "SHAREPOINT_CLIENT_ID / SHAREPOINT_CLIENT_SECRET)")
    cache: Dict[str, Any] = {"token": None, "expires": 0.0}  # nosec B105 - the in-memory cache starts empty

    def get() -> str:
        if cache["token"] and clock() < cache["expires"] - 60:
            return cache["token"]
        form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client_id,
                                       "client_secret": secret, "scope": SCOPE}).encode("utf-8")
        status, body, _headers = transport("POST", TOKEN_URL.format(tenant=urllib.parse.quote(tenant)),
                                           {"Content-Type": "application/x-www-form-urlencoded"}, form)
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            data = {}
        if status != 200 or not data.get("access_token"):
            raise GraphError(f"the access token could not be obtained (HTTP {status})", status)
        cache["token"], cache["expires"] = data["access_token"], clock() + float(data.get("expires_in") or 300)
        return cache["token"]

    return get


class GraphClient:
    def __init__(self, token: Callable[[], str], transport: Transport = urllib_transport, *,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = 3, max_pages: int = 1000):
        self._token, self._transport, self._sleep = token, transport, sleep
        self.max_retries, self.max_pages = max_retries, max_pages

    def _request(self, url: str, *, authorized: bool = True) -> bytes:
        headers = {"Accept": "application/json"}
        if authorized:
            headers["Authorization"] = f"Bearer {self._token()}"
        for attempt in range(self.max_retries + 1):
            status, body, response_headers = self._transport("GET", url, headers, None)
            if status == 200:
                return body
            if status == 410:
                raise ResyncRequired("the delta link expired", 410)
            if status in RETRYABLE and attempt < self.max_retries:
                try:
                    wait = float(response_headers.get("retry-after", 2 ** attempt))
                except ValueError:
                    wait = float(2 ** attempt)
                self._sleep(min(max(wait, 0.0), MAX_WAIT_SECONDS))
                continue
            raise GraphError(f"Graph request failed (HTTP {status})", status)
        raise GraphError("Graph request failed", 0)

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST a JSON body (for example to create a draft). Retried only for HTTP 429: a retry after a timeout could repeat it."""
        body = json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {self._token()}"}
        for attempt in range(self.max_retries + 1):
            status, answer, response_headers = self._transport("POST", url, headers, body)
            if status in (200, 201):
                return json.loads(answer.decode("utf-8"))
            if status == 429 and attempt < self.max_retries:
                try:
                    wait = float(response_headers.get("retry-after", 2 ** attempt))
                except ValueError:
                    wait = float(2 ** attempt)
                self._sleep(min(max(wait, 0.0), MAX_WAIT_SECONDS))
                continue
            raise GraphError(f"Graph request failed (HTTP {status})", status)
        raise GraphError("Graph request failed", 0)

    def delta_pages(self, drive_id: str, delta_link: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """The pages of the drive's delta feed; the last one carries @odata.deltaLink."""
        url = delta_link or f"{GRAPH}/drives/{urllib.parse.quote(drive_id)}/root/delta"
        for _ in range(self.max_pages):
            page = json.loads(self._request(url).decode("utf-8"))
            yield page
            url = page.get("@odata.nextLink")
            if not url:
                return
        raise GraphError("the delta feed has too many pages")

    def download(self, url: str, *, max_bytes: int) -> bytes:
        data = self._request(url, authorized=False)  # the download URL carries its own short-lived authorization
        if len(data) > max_bytes:
            raise GraphError("a downloaded file is larger than the limit")
        return data


def default_client() -> GraphClient:
    return GraphClient(token_provider())


# --- items ---------------------------------------------------------------------------------------------


def safe_relative(raw: str) -> Optional[str]:
    """The posix relative path, or None when it could leave the mirror or is not a plain path."""
    text = unicodedata.normalize("NFC", raw or "")
    if not text or "\\" in text or text.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in text):
        return None
    parts = text.split("/")  # not PurePosixPath: it would quietly collapse "a//b" and "./a"
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _relative_in_folder(item: Dict[str, Any], folder: str) -> Optional[str]:
    """The path of a file item relative to the synced folder; None when it is outside (or has no usable path)."""
    parent = str((item.get("parentReference") or {}).get("path") or "")
    if "root:" not in parent or not item.get("name"):
        return None
    full = parent.split("root:", 1)[1].rstrip("/") + "/" + str(item["name"])
    prefix = folder.rstrip("/") + "/"
    return full[len(prefix):] if full.startswith(prefix) else None


def _state_file(source_id: str) -> Path:
    return registry.sources_dir() / "state" / f"{source_id}.sharepoint.json"


def _load_state(source_id: str) -> Dict[str, Any]:
    try:
        data = json.loads(_state_file(source_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"delta_link": None, "items": {}}
    except (OSError, ValueError) as exc:
        raise SourceError("the SharePoint sync state is unreadable") from exc
    return data if isinstance(data, dict) and isinstance(data.get("items"), dict) else {"delta_link": None, "items": {}}


def _save_state(source_id: str, state: Dict[str, Any]) -> None:
    registry._write_json(_state_file(source_id), state)


def _signature(item: Dict[str, Any]) -> str:
    hashes = (item.get("file") or {}).get("hashes") or {}
    return str(hashes.get("sha256Hash") or hashes.get("quickXorHash") or item.get("lastModifiedDateTime") or "")


def _remove(mirror: Path, rel: str) -> bool:
    target = mirror / rel
    if target.is_file():
        target.unlink()
        return True
    return False


def pull(record: Dict[str, Any], *, client: Optional[GraphClient] = None) -> Dict[str, Any]:
    """Bring the local mirror of a sharepoint source up to date with its delta feed. Returns counts (never file names)."""
    source_id, options = record["source_id"], record["options"]
    folder = "/" + str(options["folder"]).strip("/")
    mirror = registry.mirror_dir(source_id)
    mirror.mkdir(parents=True, exist_ok=True)
    client = client or default_client()
    state = _load_state(source_id)
    summary = {"downloaded": 0, "removed": 0, "unsupported": 0, "too_large": 0, "unsafe": 0, "hidden": 0, "unchanged": 0,
               "resync": False, "pages": 0}
    allowed, limit = supported_extensions(), max_file_bytes()
    items: Dict[str, Dict[str, Any]] = dict(state["items"])
    seen = set()
    full = state["delta_link"] is None

    def run(delta_link: Optional[str]) -> Optional[str]:
        final = None
        for page in client.delta_pages(str(options["drive_id"]), delta_link):
            summary["pages"] += 1
            final = page.get("@odata.deltaLink") or final
            for item in page.get("value") or []:
                _handle(item)
        return final

    def _handle(item: Dict[str, Any]) -> None:
        item_id = str(item.get("id") or "")
        if not item_id:
            return
        known = items.get(item_id)
        if item.get("deleted") is not None:
            if known and _remove(mirror, known["path"]):
                summary["removed"] += 1
            items.pop(item_id, None)
            return
        if "file" not in item:
            return
        rel = _relative_in_folder(item, folder)
        if rel is None:  # outside the folder (or moved out of it)
            if known and _remove(mirror, known["path"]):
                summary["removed"] += 1
            items.pop(item_id, None)
            return
        seen.add(item_id)
        name = str(item["name"])
        safe = safe_relative(rel)
        size = int(item.get("size") or 0)
        refused = None
        if name.startswith((".", "~$")):
            refused = "hidden"
        elif safe is None:
            refused = "unsafe"
        elif safe.rsplit(".", 1)[-1].lower() not in allowed or "." not in safe.rsplit("/", 1)[-1]:
            refused = "unsupported"
        elif size > limit:
            refused = "too_large"
        if refused:
            summary[refused] += 1
            if known:  # it was mirrored before and no longer qualifies (renamed to a hidden name, grown too large, ...)
                if _remove(mirror, known["path"]):
                    summary["removed"] += 1
                items.pop(item_id, None)
            return
        signature = _signature(item)
        if known and known["path"] == safe and known["signature"] == signature and (mirror / safe).is_file():
            summary["unchanged"] += 1
            return
        url = item.get("@microsoft.graph.downloadUrl")
        if not url:
            return
        data = client.download(str(url), max_bytes=limit)
        target = mirror / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.part-{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        if known and known["path"] != safe:  # renamed or moved: the old copy goes
            _remove(mirror, known["path"])
        items[item_id] = {"path": safe, "signature": signature, "size": size}
        summary["downloaded"] += 1

    try:
        final = run(state["delta_link"])
    except ResyncRequired:
        summary["resync"], full = True, True
        seen.clear()
        final = run(None)
    if full:  # a full listing: what is no longer in the library goes from the mirror
        for item_id in [i for i in items if i not in seen]:
            if _remove(mirror, items[item_id]["path"]):
                summary["removed"] += 1
            items.pop(item_id)
    _save_state(source_id, {"delta_link": final, "items": items})
    return summary
