"""Local persistence: settings + analysis results in a JSON-backed store.

Kept deliberately dependency-free (stdlib only) so the app stays self-contained.
Data lives under the user's home dir so it survives reinstalls / repackaging.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from .models import AppSettings, AnalysisResult, Disposition, Snapshot


def data_dir() -> Path:
    override = os.environ.get("UNIFI_AI_DATA_DIR")
    base = Path(override) if override else Path.home() / ".unifi-ai-config"
    base.mkdir(parents=True, exist_ok=True)
    return base


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dir = data_dir()
        self._settings_path = self._dir / "settings.json"
        self._results_path = self._dir / "results.json"
        self._snapshots_dir = self._dir / "snapshots"
        self._snapshots_dir.mkdir(exist_ok=True)

    # ----- settings -----
    def load_settings(self) -> AppSettings:
        with self._lock:
            if self._settings_path.exists():
                raw = json.loads(self._settings_path.read_text())
                return AppSettings.model_validate(raw)
            return AppSettings()

    def save_settings(self, settings: AppSettings) -> AppSettings:
        with self._lock:
            self._settings_path.write_text(settings.model_dump_json(indent=2))
            return settings

    # ----- snapshots (one file each; they can be large) -----
    def save_snapshot(self, snap: Snapshot) -> Snapshot:
        with self._lock:
            path = self._snapshots_dir / f"{snap.id}.json"
            path.write_text(snap.model_dump_json(indent=2))
            return snap

    def get_snapshot(self, snap_id: str) -> Optional[Snapshot]:
        with self._lock:
            path = self._snapshots_dir / f"{snap_id}.json"
            if not path.exists():
                return None
            return Snapshot.model_validate_json(path.read_text())

    def list_snapshots(self) -> list[Snapshot]:
        with self._lock:
            snaps = []
            for path in self._snapshots_dir.glob("*.json"):
                try:
                    snaps.append(Snapshot.model_validate_json(path.read_text()))
                except Exception:
                    continue
            return sorted(snaps, key=lambda s: s.created_at, reverse=True)

    # ----- analysis results -----
    def _read_results(self) -> list[AnalysisResult]:
        if not self._results_path.exists():
            return []
        raw = json.loads(self._results_path.read_text())
        return [AnalysisResult.model_validate(r) for r in raw]

    def _write_results(self, results: list[AnalysisResult]) -> None:
        self._results_path.write_text(
            json.dumps([json.loads(r.model_dump_json()) for r in results], indent=2)
        )

    def list_results(self) -> list[AnalysisResult]:
        with self._lock:
            return sorted(self._read_results(), key=lambda r: r.created_at, reverse=True)

    def get_result(self, result_id: str) -> Optional[AnalysisResult]:
        with self._lock:
            for r in self._read_results():
                if r.id == result_id:
                    return r
            return None

    def save_result(self, result: AnalysisResult) -> AnalysisResult:
        with self._lock:
            results = self._read_results()
            results = [r for r in results if r.id != result.id]
            results.append(result)
            self._write_results(results)
            return result

    def update_issue_disposition(
        self, result_id: str, issue_id: str, disposition: Disposition, note: str = ""
    ) -> Optional[AnalysisResult]:
        with self._lock:
            results = self._read_results()
            target = None
            for r in results:
                if r.id == result_id:
                    target = r
                    break
            if target is None:
                return None
            for issue in target.issues:
                if issue.id == issue_id:
                    issue.disposition = disposition
                    if note:
                        issue.note = note
                    break
            self._write_results(results)
            return target


store = Store()
