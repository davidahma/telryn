"""HTTPS transport for explicit vendor operations. Offline audits never open a socket."""

import base64
import json
import os
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import InputError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def setting(name):
    value = os.environ.get(name, "")
    if not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise InputError(f"set {name} to a valid value in the environment")
    return value


def slug(value, name):
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", value
    ):
        raise InputError(f"invalid {name}")
    return value


def query(path, **params):
    return path + "?" + urlencode(params)


class Client:
    """Create only TLS connections to the vendor configured by the operator."""

    def __init__(self, provider):
        self.provider = provider
        prefix = "TELRYN_" + provider.upper() + "_"
        self.headers = {"Accept": "application/json"}
        if provider == "intercom":
            region = os.environ.get(prefix + "REGION", "us")
            regions = {
                "us": "api.intercom.io",
                "eu": "api.eu.intercom.io",
                "au": "api.au.intercom.io",
            }
            if region not in regions:
                raise InputError("Intercom region must be us, eu, or au")
            self.host = regions[region]
            self.scope = slug(setting(prefix + "WORKSPACE"), "workspace")
            self.headers["Intercom-Version"] = "2.14"
        elif provider == "decagon":
            self.host = "api.decagon.ai"
            self.scope = slug(setting(prefix + "WORKSPACE"), "workspace")
        elif provider == "salesforce":
            self.host = setting(prefix + "HOST").lower()
            if not re.fullmatch(
                r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.my\.salesforce\.com", self.host
            ):
                raise InputError("use your Salesforce My Domain hostname")
            self.scope = self.host
        else:
            self.scope = setting(prefix + "SUBDOMAIN").lower()
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", self.scope):
                raise InputError("invalid tenant subdomain")
            domain = "zendesk" if provider == "zendesk_qa" else provider
            self.host = self.scope + "." + domain + ".com"
        token = setting(prefix + "TOKEN")
        if provider == "freshdesk":
            credential = base64.b64encode((token + ":X").encode()).decode()
            self.headers["Authorization"] = "Basic " + credential
        else:
            self.headers["Authorization"] = "Bearer " + token
        self.opener = build_opener(NoRedirect())

    def request(self, path, *, method="GET", body=None):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
            or "#" in path
        ):
            raise InputError("invalid vendor request path")
        headers = self.headers.copy()
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, allow_nan=False).encode()
        req = Request(
            "https://" + self.host + path, headers=headers, data=data, method=method
        )
        # Search POST requests are safe reads. Only retry transient read errors.
        read = method == "GET" or (method == "POST" and path == "/conversations/search")
        for attempt in range(3):
            try:
                with self.opener.open(req, timeout=30) as response:
                    if not 200 <= response.status < 300:
                        raise InputError(
                            f"{self.provider} returned HTTP {response.status}"
                        )
                    raw = response.read(32 * 1024 * 1024 + 1)
                    if len(raw) > 32 * 1024 * 1024:
                        raise InputError(f"{self.provider} page exceeds 32 MiB")
                    return json.loads(raw), response.headers
            except HTTPError as exc:
                with exc:
                    delay = exc.headers.get("Retry-After", str(2**attempt))
                    if (
                        read
                        and attempt < 2
                        and exc.code in (429, 502, 503, 504)
                        and delay.isdecimal()
                        and len(delay) <= 2
                        and int(delay) <= 10
                    ):
                        time.sleep(max(1, int(delay)))
                        continue
                    raise InputError(
                        f"{self.provider} returned HTTP {exc.code}"
                    ) from None
            except (URLError, HTTPException, TimeoutError, OSError) as exc:
                raise InputError(
                    f"{self.provider} connection failed ({type(exc).__name__})"
                ) from None
            except (UnicodeError, json.JSONDecodeError):
                raise InputError(f"{self.provider} returned invalid JSON") from None

    def intercom_identity(self):
        result, _ = self.request("/me")
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("app"), dict)
            or result["app"].get("id_code") != self.scope
        ):
            raise InputError(
                "Intercom token belongs to a different or unknown workspace"
            )
        return slug(result.get("id"), "Intercom admin ID")
