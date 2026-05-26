"""Local HTTP API and static server for the squad operations console."""

from __future__ import annotations

import argparse
import hmac
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import app.config  # Load local runtime settings before exposing dashboard status.
from app.execution_engine import (
    apply_execution_request,
    decide_apply_execution,
    decide_execution_request,
    prepare_execution_request,
    rollback_execution_request,
)
from app.operational_store import (
    DEFAULT_DB_PATH,
    automation_demand_snapshot,
    board_snapshot,
    cost_snapshot,
    create_parking_lot_item,
    execution_request_snapshot,
    human_decision_snapshot,
    metrics_snapshot,
    parking_lot_snapshot,
    pending_work_snapshot,
    promote_parking_lot_item,
    record_automation_demand,
    recent_runs_snapshot,
    respond_human_decision,
    run_flow_snapshot,
    workflow_checkpoint_snapshot,
)
from app.scoped_storage import read_scoped_or_seed
from app.run_service import enqueue_manual_run, preview_manual_run


BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = BASE_DIR / "app" / "ui"
MAX_BODY_BYTES = 1_100_000


class OperationsHandler(SimpleHTTPRequestHandler):
    db_path: Path = DEFAULT_DB_PATH
    webhook_token: str = ""
    run_starter = staticmethod(enqueue_manual_run)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body exceeds the API size limit.")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _reject_cross_origin_mutation(self) -> bool:
        origin = self.headers.get("Origin", "").rstrip("/")
        if not origin:
            return False
        expected_origin = f"http://{self.headers.get('Host', '')}".rstrip("/")
        if hmac.compare_digest(origin, expected_origin):
            return False
        self._json({"error": "Cross-origin mutation is not allowed."}, HTTPStatus.FORBIDDEN)
        return True

    def _request_id_and_action(self) -> tuple[str, str] | None:
        parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["api", "requests"]:
            return parts[2], parts[3]
        return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({"status": "ok"})
            return
        if path == "/api/dashboard":
            self._json(
                {
                    "metrics": metrics_snapshot(self.db_path, include_validation=False),
                    "pending": pending_work_snapshot(self.db_path),
                    "recent_runs": recent_runs_snapshot(limit=100, include_validation=False, db_path=self.db_path),
                    "demands": automation_demand_snapshot(self.db_path),
                    "human_decisions": human_decision_snapshot(self.db_path),
                }
            )
            return
        if path == "/api/board":
            self._json(board_snapshot(self.db_path))
            return
        if path == "/api/costs":
            self._json(cost_snapshot(self.db_path))
            return
        if path == "/api/decisions":
            self._json(human_decision_snapshot(self.db_path))
            return
        if path == "/api/flow":
            run_id = parse_qs(urlparse(self.path).query).get("run_id", [""])[0][:120]
            self._json(run_flow_snapshot(run_id=run_id or None, db_path=self.db_path))
            return
        if path == "/api/ideas":
            self._json(parking_lot_snapshot(self.db_path))
            return
        if path == "/api/requests":
            self._json(execution_request_snapshot(db_path=self.db_path))
            return
        if path == "/api/demands":
            self._json(automation_demand_snapshot(self.db_path))
            return
        if path == "/api/memory":
            namespace = parse_qs(urlparse(self.path).query).get("namespace", [""])[0][:250]
            self._json(
                {
                    "memory_namespace": namespace,
                    "compact_memory": read_scoped_or_seed("compact_memory.md", namespace)[-6000:],
                    "decision_log": read_scoped_or_seed("decision_log.md", namespace)[-6000:],
                }
            )
            return
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) == 3 and parts[:2] == ["api", "requests"]:
            snapshot = execution_request_snapshot(request_id=parts[2], db_path=self.db_path)
            if not snapshot["requests"]:
                self._json({"error": "Execution request not found."}, HTTPStatus.NOT_FOUND)
            else:
                self._json(snapshot["requests"][0])
            return
        if len(parts) == 4 and parts[:2] == ["api", "threads"] and parts[3] == "timeline":
            self._json(workflow_checkpoint_snapshot(thread_id=parts[2], db_path=self.db_path))
            return
        if path in {"/", "/index.html"}:
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/intake/preview":
            if self._reject_cross_origin_mutation():
                return
            self._preview_manual_run()
            return
        if path == "/api/runs":
            if self._reject_cross_origin_mutation():
                return
            self._start_manual_run()
            return
        if path == "/api/ideas":
            if self._reject_cross_origin_mutation():
                return
            self._create_idea()
            return
        if path == "/api/hooks/n8n/demands":
            self._receive_automation_demand()
            return
        if self._reject_cross_origin_mutation():
            return
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["api", "decisions"] and parts[3] == "respond":
            self._respond_human_decision(parts[2])
            return
        if len(parts) == 4 and parts[:2] == ["api", "ideas"] and parts[3] == "promote":
            self._promote_idea(parts[2])
            return
        route = self._request_id_and_action()
        if route is None:
            self._json({"error": "Route not found."}, HTTPStatus.NOT_FOUND)
            return
        request_id, action = route
        try:
            payload = self._read_json()
            actor = str(payload.get("by", "interface")).strip() or "interface"
            notes = str(payload.get("notes", ""))
            if action in {"approve", "reject"}:
                result = decide_execution_request(
                    request_id,
                    decision="approved" if action == "approve" else "rejected",
                    decided_by=actor,
                    notes=notes,
                    db_path=self.db_path,
                )
            elif action == "prepare":
                result = prepare_execution_request(
                    request_id,
                    patch_text=str(payload.get("patch_text", "")),
                    workspace_root=str(payload.get("workspace_root", ".")),
                    db_path=self.db_path,
                )
            elif action in {"approve-apply", "reject-apply"}:
                result = decide_apply_execution(
                    request_id,
                    decision="approved" if action == "approve-apply" else "rejected",
                    decided_by=actor,
                    notes=notes,
                    db_path=self.db_path,
                )
            elif action == "apply":
                result = apply_execution_request(
                    request_id,
                    workspace_root=str(payload.get("workspace_root", ".")),
                    validations=payload.get("validations") or ["git_diff_check"],
                    db_path=self.db_path,
                )
            elif action == "rollback":
                result = rollback_execution_request(
                    request_id,
                    workspace_root=str(payload.get("workspace_root", ".")),
                    reason=notes or "Rollback requested from operations console.",
                    db_path=self.db_path,
                )
            else:
                self._json({"error": "Action not found."}, HTTPStatus.NOT_FOUND)
                return
            self._json(result)
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _preview_manual_run(self) -> None:
        try:
            self._json(preview_manual_run(self._read_json()))
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _start_manual_run(self) -> None:
        try:
            result = self.run_starter(self._read_json(), db_path=self.db_path)
            self._json(result, HTTPStatus.ACCEPTED)
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _respond_human_decision(self, decision_id: str) -> None:
        try:
            payload = self._read_json()
            result = respond_human_decision(
                decision_id,
                response=str(payload.get("response", "")),
                resolution=str(payload.get("resolution", "")),
                decided_by=str(payload.get("by", "owner")),
                db_path=self.db_path,
            )
            self._json(result)
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _create_idea(self) -> None:
        try:
            self._json(create_parking_lot_item(self._read_json(), db_path=self.db_path), HTTPStatus.CREATED)
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _promote_idea(self, idea_id: str) -> None:
        try:
            self._json(promote_parking_lot_item(idea_id, db_path=self.db_path))
        except ValueError as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _receive_automation_demand(self) -> None:
        if not self.webhook_token:
            self._json({"error": "Automation webhook is disabled."}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        authorization = self.headers.get("Authorization", "")
        expected = f"Bearer {self.webhook_token}"
        if not hmac.compare_digest(authorization, expected):
            self._json({"error": "Unauthorized."}, HTTPStatus.UNAUTHORIZED)
            return
        try:
            payload = self._read_json()
            demand = record_automation_demand(
                event_id=str(payload.get("event_id", "")),
                source="n8n",
                project_id=str(payload.get("project_id", "default")),
                initiative_id=str(payload.get("initiative_id", "default")),
                user_goal=str(payload.get("user_goal", "")),
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                db_path=self.db_path,
            )
            self._json(demand, HTTPStatus.ACCEPTED)
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args) -> None:
        return


def make_handler(db_path: str | Path, *, webhook_token: str = "", run_starter=None):
    class BoundOperationsHandler(OperationsHandler):
        pass

    BoundOperationsHandler.db_path = Path(db_path)
    BoundOperationsHandler.webhook_token = webhook_token
    if run_starter is not None:
        BoundOperationsHandler.run_starter = staticmethod(run_starter)
    return BoundOperationsHandler


def serve(*, host: str = "127.0.0.1", port: int = 8765, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    token = os.getenv("SQUAD_WEBHOOK_TOKEN", "")
    server = ThreadingHTTPServer((host, port), make_handler(db_path, webhook_token=token))
    print(f"Squad Operations Console: http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the squad operations console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    args = parser.parse_args()
    serve(host=args.host, port=args.port, db_path=args.db_path)


if __name__ == "__main__":
    main()
