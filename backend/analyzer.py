"""Drives the security analysis: shape the data, prompt the LLM, parse findings.

Analysis is split into focused chunks (firewall, wireless, segmentation, exposure,
inventory, traffic). Each chunk is a single, small LLM call covering only the
relevant slice of the configuration, so even a modest local model (e.g.
qwen2.5-coder:7b) is never handed the entire controller at once.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, Iterator

from .llm import providers
from .models import (
    AnalysisResult,
    AutomationAction,
    Issue,
    LLMSettings,
    Remediation,
    Severity,
)

# Each chunk targets a slice of the snapshot. `config`/`traffic` list the keys
# pulled from those groups; `focus` steers the model at what matters for that slice.
ANALYSIS_CHUNKS: list[dict[str, Any]] = [
    {
        "key": "firewall",
        "label": "Firewall rules, groups & port forwarding",
        "config": ["firewall_rules", "firewall_groups", "firewall_policies", "port_forwards"],
        "traffic": [],
        "focus": (
            "Overly-permissive any-any rules, missing default-deny, rule ordering that "
            "defeats intent, IPv6 gaps, and port forwards exposing risky services "
            "(RDP/SMB/Telnet/databases/admin) to the internet."
        ),
    },
    {
        "key": "segmentation",
        "label": "Networks, VLANs, routing & isolation",
        "config": ["networks", "routing", "traffic_rules", "traffic_routes"],
        "traffic": [],
        "focus": (
            "Flat networks, missing inter-VLAN isolation, IoT/guest/trusted sharing an "
            "L2, guest network not isolated, risky static/policy routes."
        ),
    },
    {
        "key": "wireless",
        "label": "Wireless (WLANs)",
        "config": ["wlans"],
        "traffic": [],
        "focus": (
            "Open/WEP/WPA1/WPA2-only where WPA3 is viable, weak or absent PSK policy, "
            "guest WLAN not isolated, IoT WLAN not segmented, SSID/management exposure."
        ),
    },
    {
        "key": "exposure",
        "label": "Remote access & controller settings",
        "config": ["settings", "port_profiles", "user_groups"],
        "traffic": [],
        "focus": (
            "WAN-accessible management UI/SSH, UPnP, disabled threat management (IPS/IDS), "
            "absent DNS/content filtering, SNMP/Telnet, insecure protocols, weak admin posture."
        ),
    },
    {
        "key": "inventory",
        "label": "Devices & clients",
        "config": ["devices", "known_clients", "active_clients"],
        "traffic": [],
        "focus": (
            "Outdated firmware, default/unmanaged devices, unexpected or rogue clients, "
            "devices on the wrong network/VLAN."
        ),
    },
    {
        "key": "traffic",
        "label": "Traffic flows, DPI & threats",
        "config": [],
        "traffic": ["dpi_by_app", "dpi_by_category", "ips_events", "alarms"],
        "focus": (
            "Traffic indicating compromise or exfiltration, risky services (Tor, "
            "crypto-mining, P2P, plaintext protocols), and patterns in IPS events/alarms."
        ),
    },
]

SYSTEM_PROMPT = """\
You are a senior network-security auditor specializing in Ubiquiti UniFi networks.
You will be given a UniFi controller's full configuration (networks/VLANs, WLANs,
firewall rules, firewall groups, port forwards, routing, traffic rules, device and
client inventory, controller settings) plus traffic-flow telemetry (DPI application
and category breakdowns, IPS/IDS events, alarms).

Audit it rigorously for real and potential security problems. Consider, at minimum:
- Management/admin exposure (WAN-accessible UI/SSH, default or weak credentials, no MFA).
- Firewall posture: overly-permissive any-any rules, missing inter-VLAN isolation,
  rule ordering that defeats intent, IPv6 gaps, missing default-deny.
- Port forwards exposing risky services (RDP/SMB/Telnet/databases) to the internet.
- WLAN security: open/WEP/WPA(1)/WPA2-only where WPA3 is viable, weak PSKs, guest
  network not isolated, IoT not segmented.
- Network segmentation: flat networks, IoT/guest/trusted sharing an L2.
- Disabled or absent threat management (IPS/IDS), DNS/content filtering where expected.
- Traffic flows that indicate compromise, exfiltration, unexpected outbound, or risky
  services (Tor, crypto-mining, P2P, plaintext protocols) and IPS events.
- Outdated firmware, insecure protocols (UPnP, mDNS leaks across VLANs), SNMP/Telnet.

Return ONLY a JSON object, no prose, with this exact shape:
{
  "summary": "2-4 sentence executive summary of overall posture",
  "issues": [
    {
      "title": "short title",
      "severity": "Critical|High|Medium|Low",
      "category": "e.g. Firewall, WLAN, Segmentation, Exposure, Traffic, ThreatMgmt",
      "description": "what is wrong and why it matters",
      "evidence": "concrete reference to the config/flow that proves it",
      "affected_objects": ["names/ids of affected networks, rules, devices"],
      "remediation": {
        "summary": "one-line fix",
        "manual_steps": ["click-by-click UniFi UI steps to fix"],
        "automation": null
      }
    }
  ]
}
Severity guide: Critical = active/trivial exploit or internet-exposed admin/RCE;
High = serious weakness likely exploitable; Medium = meaningful hardening gap;
Low = best-practice/hygiene. Be specific and reference real object names from the data.
"""


def _truncate(obj: Any, max_items: int = 60) -> Any:
    """Trim huge arrays so the payload fits comfortably in context."""
    if isinstance(obj, list):
        trimmed = [_truncate(x, max_items) for x in obj[:max_items]]
        if len(obj) > max_items:
            trimmed.append(f"...({len(obj) - max_items} more truncated)")
        return trimmed
    if isinstance(obj, dict):
        return {k: _truncate(v, max_items) for k, v in obj.items()}
    return obj


def _redact(obj: Any) -> Any:
    """Strip secrets before they ever reach an LLM."""
    sensitive = re.compile(r"(password|x_passphrase|psk|secret|priv_key|wpa_psk|key)$", re.I)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and sensitive.search(k) and isinstance(v, str) and v:
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def build_payload(collected: dict[str, Any]) -> str:
    cleaned = _redact(_truncate(collected))
    return json.dumps(cleaned, indent=2, default=str)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        return json.loads(text[start : end + 1])
    raise ValueError("LLM did not return parseable JSON")


def _coerce_issue(raw: dict) -> Issue:
    sev_raw = str(raw.get("severity", "Low")).strip().capitalize()
    severity = Severity(sev_raw) if sev_raw in Severity._value2member_map_ else Severity.LOW
    rem_raw = raw.get("remediation") or {}
    auto = None
    if isinstance(rem_raw.get("automation"), dict):
        a = rem_raw["automation"]
        try:
            auto = AutomationAction(**a)
        except Exception:
            auto = None
    remediation = Remediation(
        summary=rem_raw.get("summary", ""),
        manual_steps=rem_raw.get("manual_steps", []) or [],
        automation=auto,
    )
    return Issue(
        id=str(uuid.uuid4()),
        title=raw.get("title", "Untitled finding"),
        severity=severity,
        category=raw.get("category", "General"),
        description=raw.get("description", ""),
        evidence=raw.get("evidence", ""),
        affected_objects=raw.get("affected_objects", []) or [],
        remediation=remediation,
    )


def _chunk_payload(collected: dict[str, Any], chunk: dict[str, Any]) -> str:
    config = collected.get("config", {})
    traffic = collected.get("traffic", {})
    slice_: dict[str, Any] = {}
    for k in chunk["config"]:
        if k in config:
            slice_[k] = config[k]
    for k in chunk["traffic"]:
        if k in traffic:
            slice_[k] = traffic[k]
    cleaned = _redact(_truncate(slice_))
    return json.dumps(cleaned, indent=2, default=str)


def analyze_chunk(collected: dict[str, Any], chunk: dict[str, Any], llm: LLMSettings) -> list[Issue]:
    payload = _chunk_payload(collected, chunk)
    system = (
        SYSTEM_PROMPT
        + f"\n\nFOR THIS PASS, focus specifically on: {chunk['label']}.\n"
        + f"Pay particular attention to: {chunk['focus']}\n"
        + "Only report findings supported by the data slice below. If nothing is wrong, "
        + 'return {"summary":"","issues":[]}.'
    )
    user_msg = (
        f"Audit this slice of a UniFi controller — {chunk['label']}. JSON follows.\n\n"
        f"=== DATA ===\n{payload}"
    )
    text = providers.complete(system, user_msg, llm)
    parsed = _extract_json(text)
    issues = [_coerce_issue(r) for r in parsed.get("issues", [])]
    for i in issues:
        if i.category == "General":
            i.category = chunk["label"].split(",")[0]
    return issues


def analyze_streaming(
    collected: dict[str, Any], llm: LLMSettings, snapshot_id: str = ""
) -> Iterator[dict[str, Any]]:
    """Yield progress events per chunk, then a final 'result' event.

    Event shapes:
      {"type":"chunk_start","key","label","index","total"}
      {"type":"chunk_done","key","found":N,"index","total"}
      {"type":"chunk_error","key","error"}
      {"type":"result","result": <AnalysisResult dict>}
    """
    all_issues: list[Issue] = []
    total = len(ANALYSIS_CHUNKS)
    for idx, chunk in enumerate(ANALYSIS_CHUNKS, start=1):
        yield {"type": "chunk_start", "key": chunk["key"], "label": chunk["label"],
               "index": idx, "total": total}
        try:
            issues = analyze_chunk(collected, chunk, llm)
            all_issues.extend(issues)
            yield {"type": "chunk_done", "key": chunk["key"], "found": len(issues),
                   "index": idx, "total": total}
        except Exception as exc:  # noqa: BLE001 - one chunk failing shouldn't kill the run
            yield {"type": "chunk_error", "key": chunk["key"], "error": str(exc),
                   "index": idx, "total": total}

    all_issues.sort(key=lambda i: i.severity.rank)
    counts = {s.value: sum(1 for i in all_issues if i.severity == s) for s in Severity}
    summary = (
        f"Found {len(all_issues)} issue(s): "
        + ", ".join(f"{counts[s]} {s}" for s in ['Critical', 'High', 'Medium', 'Low'])
        + f". Analyzed across {total} focus areas using {llm.provider.value}/{llm.model}."
    )
    result = AnalysisResult(
        id=str(uuid.uuid4()),
        snapshot_id=snapshot_id,
        provider=llm.provider.value,
        model=llm.model,
        summary=summary,
        issues=all_issues,
    )
    yield {"type": "result", "result": result.model_dump(mode="json")}


def analyze(collected: dict[str, Any], llm: LLMSettings, snapshot_id: str = "") -> AnalysisResult:
    """Non-streaming convenience wrapper (runs all chunks)."""
    result = None
    for event in analyze_streaming(collected, llm, snapshot_id):
        if event["type"] == "result":
            result = AnalysisResult.model_validate(event["result"])
    assert result is not None
    return result
