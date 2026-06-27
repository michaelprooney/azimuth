#!/usr/bin/env python3
"""
Show the Garmin SSO page that triggers MFA handling in garminconnect.

By default this runs login, stops at the first MFA-like response, prints what
the page actually says, and saves the full HTML to sso_mfa_page.html.

Use --verbose for full multi-strategy probes and complete HTTP logs.

Examples:
    python3 garmin_sso_debug.py --fast
    python3 garmin_sso_debug.py --open
    python3 garmin_sso_debug.py --verbose --strategy all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Any

try:
    from curl_cffi import requests as cffi_requests

    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

import requests

from garminconnect.client import (
    IOS_LOGIN_UA,
    IOS_SSO_CLIENT_ID,
    PORTAL_SSO_CLIENT_ID,
    WIDGET_DELAY_MAX_S,
    WIDGET_DELAY_MIN_S,
    Client,
    _CSRF_RE,
    _MFARequired,
    _TITLE_RE,
)
from garminconnect.exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

SSO = "https://sso.garmin.com"
IOS_SERVICE = "https://mobile.integration.garmin.com/gcm/ios"
PORTAL_SERVICE = "https://connect.garmin.com/app"

MFA_MARKERS = (
    "mfa-code",
    "setupentermfacode",
    "enter mfa code",
    "verification code",
    "one-time code",
    "verifymfa",
)

SENSITIVE_KEYS = {"password", "mfaVerificationCode", "mfa-code"}


@dataclass
class MfaPageHit:
    strategy: str
    kind: str  # "html" or "json"
    body: str
    analysis: dict[str, Any]


class QuietLogger:
    """Collect responses without printing request/response traces."""

    def log_request(self, *args: Any, **kwargs: Any) -> None:
        return

    def log_response(self, label: str, response: Any) -> str:
        return getattr(response, "text", "") or ""

    def log_text(self, *args: Any, **kwargs: Any) -> None:
        return

    def _write(self, *args: Any, **kwargs: Any) -> None:
        return


def load_env() -> None:
    for env_path in [Path(".env"), Path(__file__).parent / ".env"]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip("\"'")
                if key and val:
                    os.environ.setdefault(key, val)


def get_credentials(args: argparse.Namespace) -> tuple[str, str]:
    load_env()
    email = args.email or os.getenv("GARMIN_EMAIL") or os.getenv("EMAIL")
    password = args.password or os.getenv("GARMIN_PASSWORD") or os.getenv("PASSWORD")
    if not email:
        email = input("Garmin email: ").strip()
    if not password:
        password = getpass("Garmin password: ")
    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        sys.exit(1)
    return email, password


def _banner(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                out[key] = "***REDACTED***"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _slug(label: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", label.strip().lower())
    return slug.strip("_") or "response"


class ResponseLogger:
    """Write complete HTTP exchanges to disk and optionally stdout."""

    def __init__(self, log_dir: Path | None, print_body: bool) -> None:
        self.log_dir = log_dir
        self.print_body = print_body
        self.step = 0
        if self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            print(f"Logging full responses under: {self.log_dir}")

    def log_request(
        self,
        label: str,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "label": label,
            "method": method,
            "url": url,
            "params": params or {},
            "headers": headers or {},
            "json": _redact(json_body) if json_body is not None else None,
            "form": _redact(form_body) if form_body is not None else None,
        }
        print(f"REQUEST [{label}]")
        print(f"  {method} {url}")
        if params:
            print(f"  params: {json.dumps(_redact(params), sort_keys=True)}")
        if json_body is not None:
            print(f"  json: {json.dumps(_redact(json_body), sort_keys=True)}")
        if form_body is not None:
            print(f"  form: {json.dumps(_redact(form_body), sort_keys=True)}")
        if self.log_dir is not None:
            self._write(f"{self._next_prefix(label)}_request.json", payload)

    def log_response(self, label: str, response: Any) -> str:
        self.step += 1
        prefix = f"{self.step:02d}_{_slug(label)}"
        url = getattr(response, "url", "")
        status = getattr(response, "status_code", "?")
        headers = dict(getattr(response, "headers", {}) or {})
        text = getattr(response, "text", "") or ""

        print(f"RESPONSE [{label}]")
        print(f"  status: {status}")
        print(f"  url: {url}")
        print(f"  headers: {json.dumps(headers, sort_keys=True)}")

        meta = {
            "label": label,
            "status_code": status,
            "url": url,
            "headers": headers,
            "body_bytes": len(text.encode("utf-8", errors="replace")),
        }

        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        body_path: Path | None = None
        if self.log_dir is not None:
            if "json" in content_type.lower():
                body_path = self._write(f"{prefix}_body.json", self._parse_json(text))
                meta["body_kind"] = "json"
            else:
                body_path = self._write_text(f"{prefix}_body.txt", text)
                meta["body_kind"] = "text"
            headers_path = self._write(f"{prefix}_headers.json", headers)
            meta["body_file"] = str(body_path)
            meta["headers_file"] = str(headers_path)
            self._write(f"{prefix}_meta.json", meta)
            print(f"  saved body: {body_path}")
        else:
            meta["body_kind"] = "json" if "json" in content_type.lower() else "text"
        if self.print_body:
            print("  body:")
            if meta["body_kind"] == "json":
                print(json.dumps(self._parse_json(text), indent=2, sort_keys=True))
            else:
                print(text)
        else:
            print(f"  body size: {meta['body_bytes']} bytes (use --print-body to echo here)")

        return text

    def log_text(self, label: str, text: str, *, kind: str = "text") -> None:
        if self.log_dir is not None:
            prefix = self._next_prefix(label)
            if kind == "json":
                path = self._write(f"{prefix}.json", self._parse_json(text))
            else:
                path = self._write_text(f"{prefix}.txt", text)
            print(f"LOG [{label}] saved to {path}")
        if self.print_body:
            print(f"LOG [{label}] body:")
            print(text)

    def _next_prefix(self, label: str) -> str:
        self.step += 1
        return f"{self.step:02d}_{_slug(label)}"

    def _write(self, name: str, data: Any) -> Path | None:
        if self.log_dir is None:
            return None
        path = self.log_dir / name
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def _write_text(self, name: str, text: str) -> Path | None:
        if self.log_dir is None:
            return None
        path = self.log_dir / name
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def _parse_json(text: str) -> Any:
        try:
            return json.loads(text)
        except Exception:
            return {"_unparsed_text": text}


def analyze_widget_html(html: str) -> dict[str, Any]:
    title_match = _TITLE_RE.search(html)
    title = title_match.group(1).strip() if title_match else "(no <title> found)"
    lower = html.lower()
    markers = [m for m in MFA_MARKERS if m in lower]
    library_mfa = "mfa" in title.lower() or "authentication application" in title.lower()
    has_password = 'name="password"' in lower
    has_sign_in = "sign in" in lower

    if markers:
        verdict = "REAL MFA page — Garmin is asking for a verification code"
    elif library_mfa and (has_password or has_sign_in):
        verdict = (
            "FALSE MFA — page title looks like MFA, but HTML is still a sign-in form. "
            "No code is sent to email."
        )
    elif library_mfa:
        verdict = "Ambiguous — title matches MFA, but no clear code-entry field found"
    elif title.lower() == "success":
        verdict = "Login succeeded at SSO (service ticket should be in HTML)"
    else:
        verdict = "Not MFA — unexpected SSO page"

    return {
        "page_title": title,
        "garminconnect_would_treat_as_mfa": library_mfa,
        "mfa_markers_in_html": markers or None,
        "has_password_field": has_password,
        "has_sign_in_text": has_sign_in,
        "verdict": verdict,
    }


def extract_visible_page_text(html: str) -> str:
    """Pull human-readable text out of Garmin SSO HTML."""
    lines: list[str] = []

    title_match = _TITLE_RE.search(html)
    if title_match:
        lines.append(f"Title: {title_match.group(1).strip()}")

    for var in ("status", "result"):
        match = re.search(rf"var\s+{var}\s*=\s*\"([^\"]+)\"", html)
        if match:
            lines.append(f"JS {var}: {match.group(1)}")

    status_match = re.search(r'id="status"[^>]*>([^<]+)', html, re.I)
    if status_match:
        lines.append(f"Status message: {status_match.group(1).strip()}")

    for level in ("h1", "h2", "h3"):
        for heading in re.findall(rf"<{level}[^>]*>([^<]+)", html, re.I):
            text = re.sub(r"\s+", " ", heading).strip()
            if text:
                lines.append(f"{level.upper()}: {text}")

    for label_match in re.finditer(
        r'<label[^>]*for="([^"]+)"[^>]*>([^<]+)', html, re.I
    ):
        field_id = label_match.group(1)
        label = re.sub(r"\s+", " ", label_match.group(2)).strip()
        lines.append(f"Field: {label} (id={field_id})")

    for name in ("mfa-code", "username", "password"):
        if re.search(rf'name="{re.escape(name)}"', html, re.I):
            lines.append(f"Input present: {name}")

    return "\n".join(lines)


def display_mfa_page(hit: MfaPageHit, output: Path, open_browser: bool) -> None:
    _banner(f"MFA page from {hit.strategy}")
    print(json.dumps(hit.analysis, indent=2, sort_keys=True))
    print()
    print(hit.analysis.get("verdict", ""))
    print()

    if hit.kind == "html":
        print("Visible page content:")
        print("-" * 72)
        print(extract_visible_page_text(hit.body))
        print("-" * 72)
        output.write_text(hit.body, encoding="utf-8")
        print()
        print(f"Full HTML saved to: {output.resolve()}")
        if open_browser:
            webbrowser.open(output.resolve().as_uri())
            print("Opened in your default browser.")
        return

    print("JSON MFA response:")
    print("-" * 72)
    try:
        print(json.dumps(json.loads(hit.body), indent=2, sort_keys=True))
    except Exception:
        print(hit.body)
    print("-" * 72)
    output.write_text(hit.body, encoding="utf-8")
    print()
    print(f"Full JSON saved to: {output.resolve()}")


def mfa_hit_from_json(strategy: str, body: dict[str, Any]) -> MfaPageHit:
    mfa_info = body.get("customerMfaInfo") or {}
    method = mfa_info.get("mfaLastMethodUsed", "email")
    analysis = {
        "responseStatus.type": body.get("responseStatus", {}).get("type"),
        "customerMfaInfo.mfaLastMethodUsed": method,
        "garminconnect_would_treat_as_mfa": True,
        "verdict": (
            f"Garmin API returned MFA_REQUIRED via {method!r}. "
            "Check email or authenticator app for a code."
        ),
    }
    return MfaPageHit(strategy, "json", json.dumps(body, indent=2), analysis)


def _interpret_json_body(
    body: dict[str, Any], status_code: int, strategy: str, *, quiet: bool = False
) -> tuple[str, MfaPageHit | None]:
    resp_type = body.get("responseStatus", {}).get("type")
    if not quiet:
        print(f"responseStatus.type: {resp_type!r}")

    if resp_type == "MFA_REQUIRED":
        mfa_info = body.get("customerMfaInfo") or {}
        method = mfa_info.get("mfaLastMethodUsed", "email")
        if not quiet:
            print(f"customerMfaInfo.mfaLastMethodUsed: {method!r}")
            print("RESULT: Garmin explicitly returned MFA_REQUIRED")
        return "mfa", mfa_hit_from_json(strategy, body)

    if resp_type == "SUCCESSFUL":
        if not quiet:
            print("RESULT: login succeeded — Garmin returned a service ticket")
        return "success", None

    if resp_type == "INVALID_USERNAME_PASSWORD":
        if not quiet:
            print("RESULT: wrong email or password")
        return "auth_error", None

    if status_code == 429 or body.get("error", {}).get("status-code") == "429":
        if not quiet:
            print("RESULT: rate limited (429)")
        return "rate_limit", None

    if not quiet:
        print(f"RESULT: unexpected response type {resp_type!r}")
    return "error", None


def probe_mobile(
    email: str, password: str, use_cffi: bool, logger: Any, *, quiet: bool = False
) -> tuple[str, MfaPageHit | None]:
    name = "mobile+cffi" if use_cffi else "mobile+requests"
    if not quiet:
        _banner(f"Strategy: {name}")

    params = {
        "clientId": IOS_SSO_CLIENT_ID,
        "locale": "en-US",
        "service": IOS_SERVICE,
    }
    headers = {
        "User-Agent": IOS_LOGIN_UA,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": SSO,
    }
    payload = {
        "username": email,
        "password": password,
        "rememberMe": True,
        "captchaToken": "",
    }
    url = f"{SSO}/mobile/api/login"

    if use_cffi:
        if not HAS_CFFI:
            print("RESULT: skipped — curl_cffi not installed")
            return "skipped", None
        sess: Any = cffi_requests.Session(impersonate="chrome", timeout=30)
    else:
        sess = requests.Session()

    logger.log_request(
        f"{name} login",
        method="POST",
        url=url,
        params=params,
        headers=headers,
        json_body=payload,
    )

    try:
        r = sess.post(url, params=params, headers=headers, json=payload, timeout=30)
    except Exception as exc:
        print(f"RESULT: connection error — {exc}")
        return "error", None

    text = logger.log_response(f"{name} login", r)
    try:
        body = json.loads(text)
    except Exception:
        print("RESULT: response was not JSON despite Content-Type")
        return "error", None

    return _interpret_json_body(body, r.status_code, name, quiet=quiet)


def probe_widget(
    email: str, password: str, logger: Any, fast: bool, *, quiet: bool = False
) -> tuple[str, MfaPageHit | None]:
    if not quiet:
        _banner("Strategy: widget+cffi (HTML form at sso.garmin.com/sso/signin)")
    if not HAS_CFFI:
        print("RESULT: skipped — curl_cffi not installed")
        return "skipped", None

    sess = cffi_requests.Session(impersonate="chrome", timeout=30)
    sso_base = f"{SSO}/sso"
    sso_embed = f"{sso_base}/embed"
    embed_params = {
        "id": "gauth-widget",
        "embedWidget": "true",
        "gauthHost": sso_base,
    }
    signin_params = {
        **embed_params,
        "gauthHost": sso_embed,
        "service": sso_embed,
        "source": sso_embed,
        "redirectAfterAccountLoginUrl": sso_embed,
        "redirectAfterAccountCreationUrl": sso_embed,
    }

    logger.log_request(
        "widget embed GET",
        method="GET",
        url=sso_embed,
        params=embed_params,
    )
    r = sess.get(sso_embed, params=embed_params)
    logger.log_response("widget embed GET", r)
    if r.status_code == 429:
        print("RESULT: rate limited on embed GET")
        return "rate_limit", None

    signin_get_url = f"{sso_base}/signin"
    logger.log_request(
        "widget signin GET",
        method="GET",
        url=signin_get_url,
        params=signin_params,
        headers={"Referer": sso_embed},
    )
    r = sess.get(signin_get_url, params=signin_params, headers={"Referer": sso_embed})
    signin_html = logger.log_response("widget signin GET", r)
    if r.status_code == 429:
        print("RESULT: rate limited on signin GET")
        return "rate_limit", None

    csrf_match = _CSRF_RE.search(signin_html)
    if not csrf_match:
        print("RESULT: missing CSRF token in signin page")
        return "error", None

    if fast:
        delay = 0.0
    else:
        delay = (WIDGET_DELAY_MIN_S + WIDGET_DELAY_MAX_S) / 2
    if not quiet:
        print(f"Waiting {delay:.1f}s before credential POST...")
    if delay:
        time.sleep(delay)

    form_body = {
        "username": email,
        "password": password,
        "embed": "true",
        "_csrf": csrf_match.group(1),
    }
    logger.log_request(
        "widget signin POST",
        method="POST",
        url=signin_get_url,
        params=signin_params,
        headers={"Referer": r.url},
        form_body=form_body,
    )
    r = sess.post(
        signin_get_url,
        params=signin_params,
        headers={"Referer": r.url},
        data=form_body,
        timeout=30,
    )
    post_html = logger.log_response("widget signin POST", r)

    if r.status_code == 429:
        print("RESULT: rate limited on signin POST")
        return "rate_limit", None

    analysis = analyze_widget_html(post_html)
    hit: MfaPageHit | None = None
    if analysis["garminconnect_would_treat_as_mfa"]:
        hit = MfaPageHit("widget+cffi", "html", post_html, analysis)

    if not quiet:
        for key, value in analysis.items():
            print(f"{key}: {value!r}")
        if hasattr(logger, "_write"):
            logger._write("widget_analysis.json", analysis)

    if analysis["garminconnect_would_treat_as_mfa"]:
        if not quiet:
            if analysis["mfa_markers_in_html"]:
                print("RESULT: widget path would stop for MFA — looks like a real code prompt")
            else:
                print(
                    "RESULT: widget path would stop for MFA — "
                    "likely a FALSE prompt (no code sent)"
                )
        outcome = "mfa" if analysis["mfa_markers_in_html"] else "false_mfa"
        return outcome, hit

    if analysis["page_title"].lower() == "success":
        if not quiet:
            print("RESULT: widget login succeeded")
        return "success", None

    if not quiet:
        print("RESULT: widget login did not succeed")
    return "error", None


def probe_portal(
    email: str, password: str, use_cffi: bool, logger: Any, fast: bool, *, quiet: bool = False
) -> tuple[str, MfaPageHit | None]:
    name = "portal+cffi" if use_cffi else "portal+requests"
    if not quiet:
        _banner(f"Strategy: {name}")

    signin_url = f"{SSO}/portal/sso/en-US/sign-in"
    params = {
        "clientId": PORTAL_SSO_CLIENT_ID,
        "locale": "en-US",
        "service": PORTAL_SERVICE,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": SSO,
        "Referer": f"{signin_url}?clientId={PORTAL_SSO_CLIENT_ID}&service={PORTAL_SERVICE}",
    }
    payload = {
        "username": email,
        "password": password,
        "rememberMe": True,
        "captchaToken": "",
    }

    if use_cffi:
        if not HAS_CFFI:
            print("RESULT: skipped — curl_cffi not installed")
            return "skipped", None
        sess: Any = cffi_requests.Session(impersonate="chrome", timeout=30)
    else:
        sess = requests.Session()

    logger.log_request(
        f"{name} signin GET",
        method="GET",
        url=signin_url,
        params={"clientId": PORTAL_SSO_CLIENT_ID, "service": PORTAL_SERVICE},
        headers={"User-Agent": headers["User-Agent"]},
    )
    r = sess.get(
        signin_url,
        params={"clientId": PORTAL_SSO_CLIENT_ID, "service": PORTAL_SERVICE},
        headers={"User-Agent": headers["User-Agent"]},
        timeout=30,
    )
    logger.log_response(f"{name} signin GET", r)
    if r.status_code == 429:
        print("RESULT: rate limited on portal signin GET")
        return "rate_limit", None

    if not fast:
        print("Waiting 15s before credential POST (portal anti-bot delay)...")
        time.sleep(15)

    login_url = f"{SSO}/portal/api/login"
    logger.log_request(
        f"{name} login",
        method="POST",
        url=login_url,
        params=params,
        headers=headers,
        json_body=payload,
    )

    try:
        r = sess.post(login_url, params=params, headers=headers, json=payload, timeout=30)
    except Exception as exc:
        print(f"RESULT: connection error — {exc}")
        return "error", None

    text = logger.log_response(f"{name} login", r)
    try:
        body = json.loads(text)
    except Exception:
        print("RESULT: response was not JSON despite Content-Type")
        return "error", None

    return _interpret_json_body(body, r.status_code, name, quiet=quiet)


def probe_via_library(email: str, password: str, strategy: str, logger: ResponseLogger, fast: bool) -> str:
    _banner(f"Library replay: {strategy} (via garminconnect Client)")
    client = Client()
    client.verify_login = False

    runners = {
        "mobile+cffi": client._mobile_login_cffi,
        "mobile+requests": client._mobile_login_requests,
        "widget+cffi": client._widget_web_login,
        "portal+cffi": client._portal_web_login_cffi,
        "portal+requests": client._portal_web_login_requests,
    }
    run = runners[strategy]

    original_sleep = time.sleep

    def _maybe_fast_sleep(seconds: float) -> None:
        if fast and seconds >= 3:
            print(f"(fast mode: skipping {seconds:.0f}s anti-bot delay)")
            return
        original_sleep(seconds)

    time.sleep = _maybe_fast_sleep  # type: ignore[assignment]
    try:
        run(email, password)
    except _MFARequired:
        flow = getattr(client, "_mfa_flow", "?")
        method = getattr(client, "_mfa_method", "?")
        print("RESULT: library raised MFA_REQUIRED")
        print(f"  mfa_flow: {flow!r}")
        print(f"  mfa_method: {method!r}")
        if flow == "widget":
            resp = getattr(client, "_widget_last_resp", None)
            html = getattr(resp, "text", "") or ""
            logger.log_text("library widget response", html)
            analysis = analyze_widget_html(html)
            logger._write("library_widget_analysis.json", analysis)
            for key, value in analysis.items():
                print(f"  {key}: {value!r}")
            if analysis["mfa_markers_in_html"]:
                return "mfa"
            if analysis["garminconnect_would_treat_as_mfa"]:
                return "false_mfa"
        return "mfa"
    except GarminConnectAuthenticationError as exc:
        print(f"RESULT: auth error — {exc}")
        return "auth_error"
    except GarminConnectTooManyRequestsError as exc:
        print(f"RESULT: rate limited — {exc}")
        return "rate_limit"
    except GarminConnectConnectionError as exc:
        print(f"RESULT: connection error — {exc}")
        return "error"
    except Exception as exc:
        print(f"RESULT: error — {exc}")
        return "error"
    finally:
        time.sleep = original_sleep  # type: ignore[assignment]

    print("RESULT: login succeeded through garminconnect")
    return "success"


def run_strategy(
    name: str,
    email: str,
    password: str,
    *,
    logger: Any,
    fast: bool,
    use_library: bool,
    quiet: bool = False,
) -> tuple[str, MfaPageHit | None]:
    if use_library:
        outcome = probe_via_library(email, password, name, logger, fast)
        return outcome, None

    if name == "mobile+cffi":
        return probe_mobile(email, password, use_cffi=True, logger=logger, quiet=quiet)
    if name == "mobile+requests":
        return probe_mobile(email, password, use_cffi=False, logger=logger, quiet=quiet)
    if name == "widget+cffi":
        return probe_widget(email, password, logger=logger, fast=fast, quiet=quiet)
    if name == "portal+cffi":
        return probe_portal(email, password, use_cffi=True, logger=logger, fast=fast, quiet=quiet)
    if name == "portal+requests":
        return probe_portal(
            email, password, use_cffi=False, logger=logger, fast=fast, quiet=quiet
        )
    raise ValueError(f"Unknown strategy: {name}")


def find_mfa_page(
    email: str,
    password: str,
    *,
    strategies: list[str],
    fast: bool,
) -> MfaPageHit | None:
    logger = QuietLogger()
    for name in strategies:
        outcome, hit = run_strategy(
            name,
            email,
            password,
            logger=logger,
            fast=fast,
            use_library=False,
            quiet=True,
        )
        if hit is not None:
            return hit
    return None


def print_summary(results: dict[str, str], log_dir: Path | None) -> None:
    _banner("Summary")
    for name, outcome in results.items():
        print(f"  {name:18} -> {outcome}")

    if log_dir is not None:
        print()
        print(f"Full response logs: {log_dir.resolve()}")

    print()
    print("How to read this:")
    print("  success     Garmin accepted credentials and returned a service ticket")
    print("  mfa         Garmin explicitly asked for verification (email or app)")
    print("  false_mfa   Widget title looks like MFA, but HTML is still a sign-in page")
    print("  rate_limit  Too many attempts — wait 30-60 minutes")
    print("  auth_error  Wrong email/password")


def default_log_dir(strategy: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("sso_debug_logs") / f"{stamp}_{_slug(strategy)}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show the Garmin SSO page that triggers MFA handling"
    )
    parser.add_argument("--email", help="Garmin account email (or use GARMIN_EMAIL in .env)")
    parser.add_argument("--password", help="Garmin password (or use GARMIN_PASSWORD in .env)")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sso_mfa_page.html"),
        help="Where to save the MFA page (default: sso_mfa_page.html)",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the saved HTML page in your default browser",
    )
    parser.add_argument(
        "--strategy",
        choices=[
            "mobile+cffi",
            "mobile+requests",
            "widget+cffi",
            "portal+cffi",
            "portal+requests",
            "all",
        ],
        default="widget+cffi",
        help="Which SSO path to probe (default: widget+cffi)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip anti-bot delays",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Run full multi-strategy probe with complete HTTP logs",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Directory for verbose HTTP logs (default: sso_debug_logs/<timestamp>/)",
    )
    parser.add_argument(
        "--print-body",
        action="store_true",
        help="With --verbose, also print complete response bodies to the terminal",
    )
    parser.add_argument(
        "--library",
        action="store_true",
        help="With --verbose, replay through garminconnect Client",
    )
    args = parser.parse_args()

    email, password = get_credentials(args)

    strategies = [
        "mobile+cffi",
        "mobile+requests",
        "widget+cffi",
        "portal+cffi",
        "portal+requests",
    ]
    if args.strategy != "all":
        strategies = [args.strategy]

    if not args.verbose:
        print("Checking Garmin SSO for an MFA page...")
        hit = find_mfa_page(email, password, strategies=strategies, fast=args.fast)
        if hit is None:
            print("No MFA-like SSO page was returned.")
            print("Try again later if you were rate-limited, or use --verbose for details.")
            sys.exit(1)
        output = args.output
        if hit.kind == "json" and output.suffix.lower() == ".html":
            output = output.with_suffix(".json")
        display_mfa_page(hit, output, args.open)
        return

    log_dir = args.log_dir or default_log_dir(args.strategy)
    logger = ResponseLogger(log_dir, print_body=args.print_body)

    print("Garmin SSO verbose probe")
    print(f"Account: {email}")
    print("Password: (hidden)")

    results: dict[str, str] = {}
    for name in strategies:
        strategy_dir = log_dir / _slug(name) if len(strategies) > 1 else log_dir
        strategy_logger = ResponseLogger(strategy_dir, print_body=args.print_body)
        outcome, hit = run_strategy(
            name,
            email,
            password,
            logger=strategy_logger,
            fast=args.fast,
            use_library=args.library,
        )
        results[name] = outcome
        if hit is not None:
            display_mfa_page(hit, args.output, args.open)
            break

    print_summary(results, log_dir)


if __name__ == "__main__":
    main()
