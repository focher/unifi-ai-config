"""FastAPI backend + static frontend server for the UniFi AI Config Auditor."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, analyzer
from .llm import providers
from .models import AppSettings, Disposition, LLMProvider, LLMSettings, Snapshot
from .storage import store
from .unifi_client import (
    MFARequired,
    SECTION_GROUPS,
    SECTION_LABELS,
    SECTION_META,
    UniFiClient,
    UniFiError,
)


def _ndjson(events: Iterator[dict]) -> StreamingResponse:
    def gen():
        for ev in events:
            yield json.dumps(ev) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")

app = FastAPI(title="UniFi AI Config Auditor", version=__version__)


@app.get("/api/version")
def get_version() -> dict:
    return {"version": __version__}


# The server binds to loopback and holds live controller credentials, so only
# requests addressed to localhost are honored. This blocks DNS-rebinding attacks
# where a malicious web page resolves its own hostname to 127.0.0.1 to reach the
# local API from the user's browser.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


@app.middleware("http")
async def _restrict_host(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0].strip("[]")
    if host and host not in {h.strip("[]") for h in _ALLOWED_HOSTS}:
        return JSONResponse(status_code=421, content={"detail": "Host not allowed."})
    return await call_next(request)


def _frontend_dir() -> Path:
    # When frozen by PyInstaller, bundled data lives under sys._MEIPASS.
    import sys
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "frontend"
    return Path(__file__).resolve().parent.parent / "frontend"


FRONTEND = _frontend_dir()


# --------------------------- settings ---------------------------

@app.get("/api/settings")
def get_settings() -> dict:
    s = store.load_settings()
    # Never ship secrets back to the browser; signal presence instead.
    data = s.model_dump()
    data["unifi"]["password"] = "***" if s.unifi.password else ""
    data["llm"]["api_key"] = "***" if s.llm.api_key else ""
    return data


@app.post("/api/settings")
def save_settings(incoming: AppSettings) -> dict:
    current = store.load_settings()
    # Preserve stored secrets when the UI sends the masked placeholder.
    if incoming.unifi.password in ("", "***"):
        incoming.unifi.password = current.unifi.password
    if incoming.llm.api_key in ("", "***"):
        incoming.llm.api_key = current.llm.api_key
    store.save_settings(incoming)
    return get_settings()


# --------------------------- discovery / connectivity ---------------------------

class MFABody(BaseModel):
    mfa_token: str = ""


# Status 428 ("Precondition Required") tells the UI to prompt for an MFA code.
MFA_STATUS = 428


@app.post("/api/unifi/test")
def test_unifi(body: MFABody = MFABody()) -> dict:
    s = store.load_settings()
    client = UniFiClient(s.unifi)
    try:
        return client.test_connection(body.mfa_token or None)
    except MFARequired as exc:
        raise HTTPException(status_code=MFA_STATUS, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        client.close()


class ModelQuery(BaseModel):
    provider: LLMProvider
    base_url: str = ""


@app.post("/api/llm/models")
def list_models(q: ModelQuery) -> dict:
    suggested = providers.SUGGESTED_MODELS.get(q.provider, [])
    installed = []
    if q.provider in (LLMProvider.OLLAMA, LLMProvider.LMSTUDIO):
        installed = providers.list_local_models(q.provider, q.base_url)
    return {"suggested": suggested, "installed": installed,
            "default_base": providers.DEFAULT_BASE[q.provider]}


# --------------------------- analysis ---------------------------

# --------------------------- step 1: collection (streaming) ---------------------------

@app.post("/api/collect")
def collect_stream(body: MFABody = MFABody()) -> StreamingResponse:
    """Step 1 — pull config + traffic from the controller, emitting per-step progress,
    then persist the result as a reusable snapshot. Streams newline-delimited JSON."""
    s = store.load_settings()
    if not s.unifi.username:
        raise HTTPException(400, "Configure UniFi connection first.")

    def events() -> Iterator[dict]:
        client = UniFiClient(s.unifi)
        try:
            try:
                client.login(body.mfa_token or None)
            except MFARequired as exc:
                yield {"type": "mfa_required", "message": str(exc)}
                return
            except Exception as exc:  # noqa: BLE001
                yield {"type": "error", "message": f"Login failed: {exc}"}
                return

            plan = client.collection_plan()
            total = len(plan)
            config: dict = {}
            traffic: dict = {}
            counts: dict = {}
            yield {"type": "start", "total": total}
            for idx, st in enumerate(plan, start=1):
                yield {"type": "step_start", "key": st["key"], "label": st["label"],
                       "group": st["group"], "index": idx, "total": total}
                value = client._safe(st["fetch"])  # noqa: SLF001
                target = config if st["group"] == "config" else traffic
                target[st["key"]] = value
                n = client.count_objects(value)
                counts[st["key"]] = n
                yield {"type": "step_done", "key": st["key"], "label": st["label"],
                       "count": n, "index": idx, "total": total,
                       "ok": n != -1}

            # Building + writing the snapshot can take a moment on large networks and
            # can fail (e.g. disk/serialization); surface both so the UI never hangs.
            yield {"type": "saving"}
            try:
                snap = Snapshot(
                    id=str(uuid.uuid4()),
                    site=s.unifi.site,
                    data={"config": config, "traffic": traffic, "site": s.unifi.site},
                    object_counts=counts,
                )
                store.save_snapshot(snap)
            except Exception as exc:  # noqa: BLE001
                yield {"type": "error", "message": f"Failed to save snapshot: {exc}"}
                return
            total_objects = sum(v for v in counts.values() if v > 0)
            yield {"type": "snapshot", "snapshot": {
                "id": snap.id, "created_at": snap.created_at.isoformat(),
                "site": snap.site, "object_counts": counts,
                "total_objects": total_objects}}
            yield {"type": "done"}
        except Exception as exc:  # noqa: BLE001 - never let the stream die silently
            yield {"type": "error", "message": f"Collection failed: {exc}"}
        finally:
            client.close()

    return _ndjson(events())


@app.get("/api/snapshots")
def list_snapshots() -> list[dict]:
    return [
        {"id": s.id, "created_at": s.created_at.isoformat(), "site": s.site,
         "object_counts": s.object_counts,
         "total_objects": sum(v for v in s.object_counts.values() if v > 0)}
        for s in store.list_snapshots()
    ]


def _section_value(snap, key: str):
    """Return (group, value) for a section key within a snapshot, or (None, None)."""
    group = SECTION_GROUPS.get(key)
    if group is None:
        return None, None
    return group, (snap.data.get(group, {}) or {}).get(key)


@app.get("/api/snapshots/{snapshot_id}/sections")
def snapshot_sections(snapshot_id: str) -> list[dict]:
    """List the data sections present in a snapshot, in canonical order."""
    snap = store.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(404, "Snapshot not found")
    out = []
    for key, label, group in SECTION_META:
        bucket = snap.data.get(group, {}) or {}
        if key not in bucket:
            continue
        count = snap.object_counts.get(key, 0)
        out.append({"key": key, "label": label, "group": group, "count": count,
                    "error": count == -1})
    return out


@app.get("/api/snapshots/{snapshot_id}/section/{key}")
def snapshot_section(snapshot_id: str, key: str, download: bool = False, redact: bool = False):
    """Return one section's collected data (for browse/copy/download).

    Pass redact=true to mask secrets (passphrases/keys) using the same rules the
    LLM analysis path applies.
    """
    snap = store.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(404, "Snapshot not found")
    group, value = _section_value(snap, key)
    if group is None or key not in (snap.data.get(group, {}) or {}):
        raise HTTPException(404, "Section not found in snapshot")
    if redact:
        value = analyzer.redact_secrets(value)
    if download:
        body = json.dumps(value, indent=2, default=str)
        suffix = "-redacted" if redact else ""
        return Response(
            content=body, media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{key}{suffix}.json"'},
        )
    return {"key": key, "label": SECTION_LABELS.get(key, key), "group": group,
            "redacted": redact, "data": value}


@app.get("/api/snapshots/{snapshot_id}/download")
def snapshot_download(snapshot_id: str, redact: bool = False):
    """Download the entire snapshot (config + traffic) as one JSON file."""
    snap = store.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(404, "Snapshot not found")
    data = analyzer.redact_secrets(snap.data) if redact else snap.data
    body = json.dumps(data, indent=2, default=str)
    stamp = snap.created_at.strftime("%Y%m%d-%H%M%S")
    suffix = "-redacted" if redact else ""
    return Response(
        content=body, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="unifi-snapshot-{stamp}{suffix}.json"'},
    )


# --------------------------- step 2: analysis (streaming, chunked) ---------------------------

class AnalyzeBody(BaseModel):
    # Section keys to analyze. Empty/omitted => analyze everything in the snapshot.
    selected: list[str] = []


@app.post("/api/analyze/{snapshot_id}")
def analyze_stream(snapshot_id: str, body: AnalyzeBody = AnalyzeBody()) -> StreamingResponse:
    """Step 2 — run the chunked LLM analysis over the selected sections of a stored
    snapshot, streaming per-chunk progress, and persist the result."""
    snap = store.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(404, "Snapshot not found")
    s = store.load_settings()
    selected = set(body.selected) if body.selected else None

    def events() -> Iterator[dict]:
        try:
            for ev in analyzer.analyze_streaming(snap.data, s.llm, snapshot_id, selected):
                if ev["type"] == "result":
                    result = analyzer.AnalysisResult.model_validate(ev["result"])
                    store.save_result(result)
                yield ev
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "message": f"Analysis failed: {exc}"}

    return _ndjson(events())


@app.get("/api/results")
def list_results() -> list[dict]:
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "model": r.model,
            "provider": r.provider,
            "summary": r.summary,
            "counts": _counts(r),
        }
        for r in store.list_results()
    ]


def _counts(result) -> dict:
    out = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for i in result.issues:
        out[i.severity.value] += 1
    return out


@app.get("/api/results/{result_id}")
def get_result(result_id: str) -> dict:
    r = store.get_result(result_id)
    if not r:
        raise HTTPException(404, "Result not found")
    return r.model_dump(mode="json")


class DispositionUpdate(BaseModel):
    disposition: Disposition
    note: str = ""


@app.post("/api/results/{result_id}/issues/{issue_id}/disposition")
def set_disposition(result_id: str, issue_id: str, body: DispositionUpdate) -> dict:
    r = store.update_issue_disposition(result_id, issue_id, body.disposition, body.note)
    if not r:
        raise HTTPException(404, "Result or issue not found")
    return r.model_dump(mode="json")


@app.post("/api/results/{result_id}/issues/{issue_id}/remediate")
def apply_remediation(result_id: str, issue_id: str) -> dict:
    """Apply a structured automation action against the controller, if present."""
    r = store.get_result(result_id)
    if not r:
        raise HTTPException(404, "Result not found")
    issue = next((i for i in r.issues if i.id == issue_id), None)
    if not issue:
        raise HTTPException(404, "Issue not found")
    auto = issue.remediation.automation
    if not auto:
        raise HTTPException(400, "This issue has no automated remediation; follow the manual steps.")
    s = store.load_settings()
    client = UniFiClient(s.unifi)
    try:
        prefix = "/proxy/network" if s.unifi.is_unifi_os else ""
        url = f"{prefix}/api/s/{s.unifi.site}{auto.endpoint}"
        resp = client.request(auto.method, url, auto.payload or None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Remediation failed: {exc}")
    finally:
        client.close()
    store.update_issue_disposition(result_id, issue_id, Disposition.REMEDIATE,
                                   "Automated remediation applied.")
    return {"ok": True, "response": resp}


# --------------------------- frontend ---------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
