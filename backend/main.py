"""FastAPI backend + static frontend server for the UniFi AI Config Auditor."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analyzer
from .llm import providers
from .models import AppSettings, Disposition, LLMProvider, LLMSettings, Snapshot
from .storage import store
from .unifi_client import MFARequired, UniFiClient, UniFiError


def _ndjson(events: Iterator[dict]) -> StreamingResponse:
    def gen():
        for ev in events:
            yield json.dumps(ev) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")

app = FastAPI(title="UniFi AI Config Auditor")


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

            snap = Snapshot(
                id=str(uuid.uuid4()),
                site=s.unifi.site,
                data={"config": config, "traffic": traffic, "site": s.unifi.site},
                object_counts=counts,
            )
            store.save_snapshot(snap)
            yield {"type": "snapshot", "snapshot": {
                "id": snap.id, "created_at": snap.created_at.isoformat(),
                "site": snap.site, "object_counts": counts}}
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


# --------------------------- step 2: analysis (streaming, chunked) ---------------------------

@app.post("/api/analyze/{snapshot_id}")
def analyze_stream(snapshot_id: str) -> StreamingResponse:
    """Step 2 — run the chunked LLM analysis over a stored snapshot, streaming
    per-chunk progress, and persist the result."""
    snap = store.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(404, "Snapshot not found")
    s = store.load_settings()

    def events() -> Iterator[dict]:
        try:
            for ev in analyzer.analyze_streaming(snap.data, s.llm, snapshot_id):
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
