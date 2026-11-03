"""FastAPI backend: login, chat, reports, subscription analysis, feedback, dashboard, TTS.

Run: python -m uvicorn nexora.api.main:app --port 8000   (or: make api)
Every endpoint except /health, /users and /login needs `Authorization: Bearer <token>`.
Role checks (security/rbac.py) are enforced here, not only in the UI.
"""


import logging
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from nexora.agents.orchestrator import Orchestrator, analysis_to_dict
from nexora.agents.report import build_report, compute_report, project_view, template_project_summary
from nexora.agents.subscription import analyze_subscriptions
from nexora.api.schemas import (
    ActionConfirmation,
    ChatRequest,
    FeedbackRequest,
    LoginRequest,
    RegisterRequest,
    TTSRequest,
)
from nexora.config import Config, load_config
from nexora.db.repositories import Frames, MissingDataError, frames_from_db
from nexora.db.session import connect
from nexora.rag.vectorstore import EmptyIndexError
from nexora.security import audit
from nexora.security.auth import AuthError, RegistrationError, User, get_user, login, read_token, register
from nexora.security.rbac import Permission, PermissionDenied, can, require
from nexora.voice.stt import MAX_AUDIO_BYTES, transcribe
from nexora.voice.tts import VoiceUnavailableError, synthesize

log = logging.getLogger("nexora.api")
FRAMES_TTL_SECONDS = 30.0  # business tables only change when `make load` runs, so reading them per request is waste


def _warm_up(orch: Orchestrator, cfg: Config) -> None:
    """Load the embedding model, reranker and vector index in the background so the first chat is fast."""
    try:
        from nexora.rag.embeddings import embed
        embed(["warm up"], cfg.embeddings.model)
        if cfg.reranker.enabled:
            from nexora.rag.reranker import _model as reranker_model
            reranker_model(cfg.reranker.model)
        orch.llm_ready()
    except Exception:  # noqa: BLE001 - warm-up is best effort; real requests report real errors
        log.warning("Model warm-up skipped", exc_info=True)


Period = Annotated[str, Query(pattern="^(weekly|monthly|yearly)$")]


def create_app(cfg: Config | None = None, orchestrator: Orchestrator | None = None) -> FastAPI:
    """Build the API. ``orchestrator`` can be injected (tests use fakes)."""
    cfg = cfg or load_config()
    orch = orchestrator or Orchestrator(cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if orchestrator is None:  # injected (test) orchestrators are fakes: nothing to warm up
            threading.Thread(target=_warm_up, args=(orch, cfg), daemon=True).start()
        yield

    app = FastAPI(title="Nexora AI", version="0.1.0", lifespan=lifespan)
    frames_cache: dict[str, tuple[float, Frames]] = {}

    def cached_frames(conn: sqlite3.Connection) -> Frames:
        """Business data as frames, reloaded at most every FRAMES_TTL_SECONDS."""
        hit = frames_cache.get("frames")
        if hit and time.monotonic() - hit[0] < FRAMES_TTL_SECONDS:
            return hit[1]
        frames = frames_from_db(conn)
        frames_cache["frames"] = (time.monotonic(), frames)
        return frames

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(cfg.path("sqlite"))
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(get_conn)]  # noqa: N806

    def current_user(conn: Conn, authorization: Annotated[str | None, Header()] = None) -> User:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError("Missing bearer token")
        return get_user(conn, read_token(authorization.split(" ", 1)[1]))

    Me = Annotated[User, Depends(current_user)]  # noqa: N806

    @app.exception_handler(PermissionDenied)
    async def _forbidden(_: Request, exc: PermissionDenied) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(AuthError)
    async def _unauthorized(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    @app.exception_handler(RegistrationError)
    async def _bad_registration(_: Request, exc: RegistrationError) -> JSONResponse:
        status = 409 if "already exists" in str(exc) else 422
        return JSONResponse({"detail": str(exc)}, status_code=status)

    @app.exception_handler(MissingDataError)
    async def _no_data(_: Request, exc: MissingDataError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(EmptyIndexError)
    async def _no_index(_: Request, exc: EmptyIndexError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    # ------------------------------------------------------------------ public
    @app.get("/health")
    def health() -> dict[str, Any]:
        ready = orch.llm_ready()
        return {"status": "ok", "llm_ready": ready, "model": cfg.llm.model,
                "mode": "llm" if ready else "extractive",
                "notice": None if ready else (
                    "Ollama is not available: answers use extractive mode (best passage with citation).")}

    @app.get("/users")
    def users(conn: Conn) -> list[dict[str, str]]:
        rows = conn.execute("SELECT employee_id, name, role, job_title FROM employees ORDER BY employee_id")
        return [dict(r) for r in rows]

    @app.post("/login")
    def do_login(body: LoginRequest, conn: Conn) -> dict[str, Any]:
        if conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 0:
            raise MissingDataError("No users exist yet")
        try:
            user, token = login(conn, body.employee_id, body.password, cfg)
        except AuthError:
            audit.log_event(conn, body.employee_id, "unknown", "login_failed")
            raise
        audit.log_event(conn, user.employee_id, user.role, "login")
        return {"token": token, "user": asdict(user)}

    @app.post("/register", status_code=201)
    def do_register(body: RegisterRequest, conn: Conn) -> dict[str, Any]:
        """Create an ``employee`` account and log it in (the role cannot be chosen by the caller)."""
        user, token = register(conn, body.name, body.email, body.password, cfg)
        audit.log_event(conn, user.employee_id, user.role, "register")
        return {"token": token, "user": asdict(user)}

    # ------------------------------------------------------------------ authenticated
    @app.post("/chat")
    def chat(body: ChatRequest, conn: Conn, user: Me) -> dict[str, Any]:
        require(user.role, Permission.DOCUMENTS)
        return asdict(orch.chat(conn, user, body.question, body.session_id, body.include_superseded))

    @app.get("/report")
    def report(conn: Conn, user: Me, period: Period = "monthly", kind: str = "finance") -> dict[str, Any]:
        if kind == "finance":
            require(user.role, Permission.FINANCE)
            result = build_report(cached_frames(conn), period, llm=orch.llm if orch.llm_ready() else None)
            data, summary, source = result.data, result.summary, result.summary_source
        elif kind == "project":
            require(user.role, Permission.PROJECT_REPORTS)
            data = project_view(compute_report(cached_frames(conn), period))
            summary, source = template_project_summary(data), "template"
        else:
            raise HTTPException(422, "kind must be 'finance' or 'project'")
        audit.log_event(conn, user.employee_id, user.role, "report", intent="report",
                        detail=f"period={period} kind={kind}")
        return {"report": data, "summary": summary, "summary_source": source}

    @app.get("/subscriptions/analysis")
    def subscription_analysis(conn: Conn, user: Me) -> dict[str, Any]:
        require(user.role, Permission.SUBSCRIPTIONS)
        analysis = analyze_subscriptions(cached_frames(conn), cfg)
        audit.log_event(conn, user.employee_id, user.role, "subscription_analysis",
                        intent="subscription", detail=f"flags={len(analysis.flags)}")
        return {**analysis_to_dict(analysis),
                "flags": [{**asdict(f), "detection_key": f.detection_key} for f in analysis.flags]}

    @app.get("/tasks")
    def tasks(conn: Conn, user: Me, status: str | None = None) -> list[dict[str, Any]]:
        if can(user.role, Permission.ALL_TASKS):
            sql, args = "SELECT * FROM tasks", []
        else:
            require(user.role, Permission.OWN_TASKS)
            sql, args = "SELECT * FROM tasks WHERE owner = ?", [user.employee_id]
        if status:
            sql += (" AND" if args else " WHERE") + " status = ?"
            args.append(status)
        return [dict(r) for r in conn.execute(sql + " ORDER BY due", args)]

    @app.post("/feedback")
    def feedback(body: FeedbackRequest, conn: Conn, user: Me) -> dict[str, str]:
        row = conn.execute("SELECT id FROM chat_history WHERE id = ? AND user_id = ? AND role = 'assistant'",
                           (body.chat_id, user.employee_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown chat message")
        with conn:
            conn.execute("INSERT INTO feedback (chat_id, user_id, rating, comment, created_at) VALUES (?,?,?,?,?)",
                         (body.chat_id, user.employee_id, body.rating, body.comment,
                          datetime.now(UTC).isoformat(timespec="seconds")))
        audit.log_event(conn, user.employee_id, user.role, "feedback",
                        detail=f"chat={body.chat_id} rating={body.rating}")
        return {"status": "saved"}

    @app.get("/dashboard")
    def dashboard(conn: Conn, user: Me) -> dict[str, Any]:
        frames = cached_frames(conn)
        out: dict[str, Any] = {"finance": None, "subscriptions": None, "tasks": None}
        if can(user.role, Permission.FINANCE):
            tx = frames.transactions
            monthly = tx.groupby("month")["amount"].agg(
                income=lambda s: s[s > 0].sum(), expenses=lambda s: -s[s < 0].sum()).reset_index()
            monthly["net"] = monthly["income"] - monthly["expenses"]
            out["finance"] = {
                "spending_by_category": compute_report(frames, "yearly")["expenses_by_category"],
                "monthly": monthly.round(2).to_dict("records")}
        if can(user.role, Permission.SUBSCRIPTIONS):
            analysis = analyze_subscriptions(frames, cfg)
            latest = frames.usage_log.sort_values("month").groupby("tool").last()
            subs = frames.subscriptions
            out["subscriptions"] = {
                "table": [{"tool": r.tool, "monthly_cost": r.monthly_cost, "seats": r.seats,
                           "active_users": int(latest.loc[r.tool, "active_users"])}
                          for r in subs.itertuples()],
                "analysis": analysis_to_dict(analysis),
                "anomalies": [{**asdict(f), "detection_key": f.detection_key} for f in analysis.flags]}
        scoped = frames.tasks if can(user.role, Permission.ALL_TASKS) else (
            frames.tasks[frames.tasks["owner"] == user.employee_id])
        out["tasks"] = {"by_status": scoped["status"].value_counts().to_dict(), "total": len(scoped),
                        "scope": "all" if can(user.role, Permission.ALL_TASKS) else "own"}
        return out

    @app.post("/actions/confirm")
    def confirm_action(body: ActionConfirmation, conn: Conn, user: Me) -> dict[str, str]:
        """Record that a person confirmed a recommendation. The prototype never changes a subscription."""
        require(user.role, Permission.SUBSCRIPTIONS)
        audit.log_event(conn, user.employee_id, user.role, "action_confirmed", intent="subscription",
                        detail=f"{body.action} {body.tool}: {body.detail or ''}".strip())
        return {"status": "recorded",
                "note": "Confirmation logged. Nexora does not cancel or change subscriptions itself."}

    @app.post("/voice/tts")
    def tts(body: TTSRequest, conn: Conn, user: Me) -> Response:
        if not cfg.voice.tts_enabled:
            raise HTTPException(503, "Text-to-speech is disabled in config.yaml (voice.tts_enabled).")
        try:
            audio = synthesize(body.text)
        except VoiceUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        audit.log_event(conn, user.employee_id, user.role, "tts", detail=f"chars={len(body.text)}")
        return Response(audio, media_type="audio/wav")

    @app.post("/voice/stt")
    async def stt(request: Request, conn: Conn, user: Me) -> dict[str, str]:
        """Speech to text: the request body is the raw audio (WAV, MP3 or OGG)."""
        audio = await request.body()
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "The audio is too large (limit 10 MB).")
        try:
            text = transcribe(audio)
        except VoiceUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        audit.log_event(conn, user.employee_id, user.role, "stt", detail=f"bytes={len(audio)}")
        return {"text": text}

    return app


app = create_app()
