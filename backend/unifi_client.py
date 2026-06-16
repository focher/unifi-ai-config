"""UniFi Network controller API client.

Supports both UniFi OS consoles (UDM / UDM-Pro / Cloud Key Gen2+ / Dream Machine,
which proxy the network app under /proxy/network and authenticate at
/api/auth/login) and the classic self-hosted Network Controller (/api/login).

Only the standard, documented-by-community Network API is used. All collection
is read-only except for explicitly-applied remediation actions.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .models import UniFiSettings


class UniFiError(Exception):
    pass


class MFARequired(UniFiError):
    """Raised when the controller needs a 2FA/MFA code to complete login."""
    pass


class UniFiClient:
    def __init__(self, settings: UniFiSettings) -> None:
        self.s = settings
        base = settings.host.rstrip("/")
        if "://" not in base:
            base = "https://" + base
        if settings.port and f":{settings.port}" not in base:
            base = f"{base}:{settings.port}"
        self.base = base
        self._client = httpx.Client(
            base_url=base,
            verify=settings.verify_ssl,
            timeout=30.0,
            follow_redirects=True,
            # A UniFi console lives on the local LAN. Ignore any HTTP(S)_PROXY /
            # NO_PROXY env vars so we connect directly — otherwise a proxy may
            # intercept the request and reject the self-signed cert (502).
            trust_env=False,
        )
        self._logged_in = False

    # ---- path helpers ----
    @property
    def _prefix(self) -> str:
        return "/proxy/network" if self.s.is_unifi_os else ""

    def _api(self, path: str) -> str:
        return f"{self._prefix}/api{path}"

    def _v2(self, path: str) -> str:
        return f"{self._prefix}/v2/api{path}"

    # ---- auth ----
    def login(self, mfa_token: Optional[str] = None) -> None:
        url = "/api/auth/login" if self.s.is_unifi_os else "/api/login"
        payload: dict[str, Any] = {
            "username": self.s.username,
            "password": self.s.password,
            "rememberMe": False,
        }
        if mfa_token:
            # UniFi OS accepts the 2FA code as "token"; classic uses "ubic_2fa_token".
            payload["token"] = mfa_token
            payload["ubic_2fa_token"] = mfa_token
        resp = self._client.post(url, json=payload)

        if resp.status_code == 200:
            # UniFi OS returns a CSRF token we must echo on mutating requests.
            token = resp.headers.get("x-csrf-token") or resp.headers.get("X-CSRF-Token")
            if token:
                self._client.headers["X-CSRF-Token"] = token
            self._logged_in = True
            return

        if self._is_mfa_challenge(resp):
            if mfa_token:
                raise MFARequired(
                    "The MFA code was rejected. Re-enter the current 6-digit code "
                    "from your authenticator app."
                )
            raise MFARequired("Multi-factor authentication code required.")

        raise UniFiError(
            f"Login failed ({resp.status_code}). Check host, credentials, and the "
            f"'UniFi OS' toggle. Response: {resp.text[:200]}"
        )

    @staticmethod
    def _is_mfa_challenge(resp: httpx.Response) -> bool:
        # UniFi OS signals a 2FA requirement via HTTP 499, or a body that names 2FA.
        if resp.status_code == 499:
            return True
        body = resp.text.lower()
        return any(k in body for k in ("2fa", "mfa", "ubic_2fa", "authenticator", "otp"))

    def _ensure(self) -> None:
        if not self._logged_in:
            self.login()

    def _get(self, url: str) -> Any:
        self._ensure()
        resp = self._client.get(url)
        if resp.status_code == 401:
            self._logged_in = False
            self.login()
            resp = self._client.get(url)
        resp.raise_for_status()
        body = resp.json()
        # Classic API wraps payloads in {"data": [...], "meta": {...}}.
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def request(self, method: str, url: str, payload: Optional[dict] = None) -> Any:
        """Used by remediation automation. Returns parsed JSON."""
        self._ensure()
        resp = self._client.request(method, url, json=payload)
        resp.raise_for_status()
        try:
            body = resp.json()
        except Exception:
            return {"status": resp.status_code}
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    def close(self) -> None:
        self._client.close()

    # ---- collection ----
    def test_connection(self, mfa_token: Optional[str] = None) -> dict:
        self.login(mfa_token)
        sites = self._get(self._api("/self/sites"))
        return {"ok": True, "sites": [s.get("name") for s in sites]}

    def _safe(self, fetch) -> Any:
        try:
            return fetch()
        except Exception as exc:  # noqa: BLE001 - endpoint may not exist on all versions
            return {"_error": str(exc)}

    def collection_plan(self) -> list[dict[str, Any]]:
        """Ordered list of collection steps for progress reporting.

        Each step: {key, label, group ('config'|'traffic'), fetch (callable)}.
        """
        site = self.s.site
        s = lambda p: self._api(f"/s/{site}{p}")  # noqa: E731
        v2 = lambda p: self._v2(f"/site/{site}{p}")  # noqa: E731

        def step(key, label, group, fn):
            return {"key": key, "label": label, "group": group, "fetch": fn}

        return [
            step("settings", "Controller settings", "config", lambda: self._get(s("/get/setting"))),
            step("networks", "Networks / VLANs", "config", lambda: self._get(s("/rest/networkconf"))),
            step("wlans", "Wireless networks (WLANs)", "config", lambda: self._get(s("/rest/wlanconf"))),
            step("firewall_rules", "Firewall rules", "config", lambda: self._get(s("/rest/firewallrule"))),
            step("firewall_groups", "Firewall groups", "config", lambda: self._get(s("/rest/firewallgroup"))),
            step("firewall_policies", "Firewall policies (zone-based)", "config", lambda: self._get(v2("/firewall-policies"))),
            step("port_forwards", "Port forwarding rules", "config", lambda: self._get(s("/rest/portforward"))),
            step("routing", "Static routes", "config", lambda: self._get(s("/rest/routing"))),
            step("traffic_rules", "Traffic rules", "config", lambda: self._get(v2("/trafficrules"))),
            step("traffic_routes", "Traffic routes / policy routing", "config", lambda: self._get(v2("/trafficroutes"))),
            step("port_profiles", "Switch port profiles", "config", lambda: self._get(s("/rest/portconf"))),
            step("user_groups", "User groups / bandwidth profiles", "config", lambda: self._get(s("/rest/usergroup"))),
            step("devices", "UniFi devices", "config", lambda: self._get(s("/stat/device"))),
            step("known_clients", "Known clients", "config", lambda: self._get(s("/rest/user"))),
            step("active_clients", "Active clients", "config", lambda: self._get(s("/stat/sta"))),
            step("dpi_by_app", "Traffic flows — by application", "traffic",
                 lambda: self.request("POST", s("/stat/sitedpi"), {"type": "by_app"})),
            step("dpi_by_category", "Traffic flows — by category", "traffic",
                 lambda: self.request("POST", s("/stat/sitedpi"), {"type": "by_cat"})),
            step("ips_events", "IPS / IDS threat events", "traffic",
                 lambda: self.request("POST", s("/stat/ips/event"), {"_limit": 200})),
            step("alarms", "Alarms", "traffic", lambda: self._get(s("/list/alarm"))),
        ]

    @staticmethod
    def count_objects(value: Any) -> int:
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict) and "_error" in value:
            return -1
        return 1 if value else 0

    def collect(self, mfa_token: Optional[str] = None) -> dict[str, Any]:
        """Pull the full configuration + traffic picture for one site (non-streaming)."""
        if not self._logged_in:
            self.login(mfa_token)
        config: dict[str, Any] = {}
        traffic: dict[str, Any] = {}
        for st in self.collection_plan():
            target = config if st["group"] == "config" else traffic
            target[st["key"]] = self._safe(st["fetch"])
        return {"config": config, "traffic": traffic, "site": self.s.site}
