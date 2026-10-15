"""Local workbench. Request-scoped connections; raw HTML is served as text only."""

import json
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .article_insights import ArticleInsights
from .candidates import Candidates, ImportCandidate
from .codex_connection import CodexConnection
from .collector import CollectionError
from .comparison import Comparison, Feedback
from .curation import Curation, CurationInput, export_note
from .discovery import Discovery, SelectionBatch
from .evaluations import Evaluations, FreezeInput, LabelInput
from .exa import search as exa_search
from .experiment import DiscoveryExperiment, SearchObservation, SearchPlan
from .inbox import FeedInbox
from .library import Library
from .model_config import ModelChoice, choose_model, selected_model
from .pipeline import Pipeline
from .processing import EditInput, ProcessingResults, ResultInput, ResultReview
from .publication import ApprovalInput, PreviewInput, Publication
from .reading_builds import BuildInput, ReadingBuilds
from .review import ReviewConflict, ReviewInput
from .searches import SearchInput, SourceSearch
from .storage import Store
from .translation import Translation
from .workflow_view import WorkflowView
from .workflows import Workflows


class NewRun(BaseModel):
    url: str


class ScreeningRequest(BaseModel):
    submission_id: str


class ConnectionAction(BaseModel):
    action: Literal["check", "login"]


class InboxDecision(BaseModel):
    action: str
    submission_id: str = ""


class DiscoveryGoal(BaseModel):
    goal: str
    as_of: date
    max_age_days: int = 90


def create_app(
    data_dir=Path("data"),
    transport=None,
    *,
    review_origin="human",
    translation_transport=None,
    reading_runner=None,
    search_provider=None,
    connection=None,
):
    connection = connection or CodexConnection(data_dir)

    @asynccontextmanager
    async def lifespan(app):
        yield
        connection.close()

    app = FastAPI(title="AI 资料流水线", version="0.1.0", lifespan=lifespan)
    frontend = Path(__file__).with_name("cactus")
    app.mount("/_astro", StaticFiles(directory=frontend / "_astro", check_dir=False), name="cactus")

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page():
        return (frontend / "setup" / "index.html").read_text(encoding="utf-8")

    @app.get("/setup/demo", response_class=HTMLResponse)
    def setup_demo():
        return (frontend / "setup" / "demo" / "index.html").read_text(encoding="utf-8")

    @app.get("/api/connection")
    def connection_state(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return connection.inspect()

    def local_connection_request(request):
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(403, "连接引导只在本机工作台使用。")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "请从本机工作台操作连接。")

    @app.post("/api/connection")
    def connection_start(request: Request, response: Response, payload: ConnectionAction):
        response.headers["Cache-Control"] = "no-store"
        local_connection_request(request)
        try:
            return connection.start(payload.action)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/connection/cancel")
    def connection_cancel(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        local_connection_request(request)
        return connection.cancel()

    @app.get("/api/model-configuration")
    def model_configuration():
        return selected_model(Path(data_dir).resolve()).public()

    @app.post("/api/model-configuration")
    def update_model_configuration(choice: ModelChoice):
        try:
            return choose_model(Path(data_dir).resolve(), choice)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/model-runs/{artifact_id}")
    def model_trace(artifact_id: str):
        import re

        from .codex_model import read_trace
        if not re.fullmatch(r"(?:insights|screening|translation)-[0-9]+|search-[a-f0-9]{32}", artifact_id):
            raise HTTPException(404, "模型记录不存在。")
        directory = Path(data_dir).resolve() / "model-runs" / artifact_id
        if not directory.is_dir():
            raise HTTPException(404, "模型记录不存在。")
        result = read_trace(directory)
        for name in ("input", "result"):
            path = directory / f"{name}.json"
            result[name] = json.loads(path.read_text("utf-8")) if path.is_file() else None
        return result

    @contextmanager
    def pipeline():
        store = Store(Path(data_dir).resolve())
        try:
            with httpx.Client(
                timeout=15,
                follow_redirects=True,
                max_redirects=5,
                transport=transport,
                headers={"User-Agent": "PracShelf/0.1 (personal reader)"},
            ) as client:
                yield Pipeline(store, client, review_origin=review_origin)
        except ReviewConflict as error:
            raise HTTPException(409, str(error)) from error
        except (ValueError, CollectionError) as error:
            raise HTTPException(400, str(error)) from error
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, "此任务已有运行中的阶段，请等待后刷新。") from error
        finally:
            store.close()

    @app.get("/", response_class=HTMLResponse)
    def index(run: str | None = None):
        if run:
            # Keep links from earlier learning notes working, including their fragment.
            from urllib.parse import quote

            return RedirectResponse("/workbench?run=" + quote(run, safe=""), status_code=307)
        return (frontend / "index.html").read_text(encoding="utf-8")

    @app.get("/workbench", response_class=HTMLResponse)
    def workbench(run: str | None = None):
        from urllib.parse import urlencode

        return RedirectResponse("/pipeline" + ("?" + urlencode({"run": run}) if run else ""), status_code=307)

    @app.get("/pipeline", response_class=HTMLResponse)
    def workflow_page():
        return (frontend / "pipeline" / "index.html").read_text(encoding="utf-8")

    @app.get("/api/workflows")
    def workflows():
        with pipeline() as flow:
            return WorkflowView(flow).list()

    @app.get("/api/workflows/{run_id}")
    def workflow_record(run_id: str, detail: Literal["summary", "full"] = "full"):
        with pipeline() as flow:
            return Workflows(flow, transport=translation_transport).inspect(run_id, detail=detail)

    def execute_workflow(identifier):
        # The HTTP request's Store has closed; the worker owns a fresh connection.
        with pipeline() as flow:
            Workflows(flow, transport=translation_transport).run(identifier)

    @app.post("/api/workflows/{run_id}/start", status_code=202)
    def start_workflow(run_id: str, payload: ScreeningRequest, tasks: BackgroundTasks):
        with pipeline() as flow:
            claimed = Workflows(flow, transport=translation_transport).begin(run_id, payload.submission_id)
        if claimed["launch"]:
            tasks.add_task(execute_workflow, claimed["id"])
        return claimed

    @app.get("/api/runs/{run_id}/insights")
    def article_insights(run_id: str, detail: Literal["summary", "full"] = "full"):
        with pipeline() as flow:
            return ArticleInsights(flow).inspect(run_id, detail=detail)

    @app.get("/api/runs/{run_id}/insights/{attempt_id}")
    def insight_attempt(run_id: str, attempt_id: int):
        with pipeline() as flow:
            return ArticleInsights(flow).attempt(run_id, attempt_id)

    def execute_insights(identifier):
        with pipeline() as flow:
            ArticleInsights(flow, transport=translation_transport).execute(identifier)

    @app.post("/api/runs/{run_id}/insights", status_code=202)
    def start_insights(run_id: str, payload: ScreeningRequest, tasks: BackgroundTasks):
        with pipeline() as flow:
            claimed = ArticleInsights(flow).begin(run_id, payload.submission_id)
        if claimed["launch"]:
            tasks.add_task(execute_insights, claimed["id"])
        return claimed

    @app.get("/read/{run_id}", response_class=HTMLResponse)
    def reader(run_id: str):
        return (frontend / "reader" / "index.html").read_text(encoding="utf-8")

    @app.get("/candidates", response_class=HTMLResponse)
    def candidate_page(id: str | None = None):
        from urllib.parse import urlencode

        run_id = None
        if id:
            with pipeline() as flow:
                run_id = Candidates(flow).inspect(id)["pipeline_id"]
        return RedirectResponse("/pipeline" + ("?" + urlencode({"run": run_id}) if run_id else ""), status_code=307)

    @app.get("/evaluations", response_class=HTMLResponse)
    def evaluation_page(case: int | None = None):
        from urllib.parse import urlencode

        run_id = None
        if case:
            with pipeline() as flow:
                run_id = Evaluations(flow).inspect(case)["snapshot"]["pipeline_id"]
        return RedirectResponse("/pipeline" + ("?" + urlencode({"run": run_id}) if run_id else ""), status_code=307)

    @app.get("/api/evaluations")
    def evaluation_report(split: str = "test"):
        with pipeline() as flow:
            return Evaluations(flow).report(split)

    @app.get("/api/evaluations/available")
    def evaluation_available():
        with pipeline() as flow:
            return Evaluations(flow).available()

    @app.post("/api/evaluations")
    def freeze_evaluation(payload: FreezeInput):
        with pipeline() as flow:
            return Evaluations(flow).freeze(payload)

    @app.get("/api/evaluations/{case_id}")
    def evaluation_case(case_id: int):
        with pipeline() as flow:
            return Evaluations(flow).inspect(case_id)

    @app.post("/api/evaluations/{case_id}/labels")
    def evaluation_label(case_id: int, payload: LabelInput):
        with pipeline() as flow:
            return Evaluations(flow, origin=review_origin).label(case_id, payload)

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox_page():
        return (frontend / "inbox" / "index.html").read_text(encoding="utf-8")

    @app.get("/search", response_class=HTMLResponse)
    def search_page():
        return (frontend / "search" / "index.html").read_text(encoding="utf-8")

    @app.get("/api/searches")
    def searches():
        with pipeline() as flow:
            return SourceSearch(flow, provider=search_provider).list()

    @app.post("/api/searches")
    def search_sources(payload: SearchInput):
        with pipeline() as flow:
            return SourceSearch(flow, provider=search_provider).search(payload)

    @app.get("/api/searches/{search_id}")
    def search_record(search_id: str):
        with pipeline() as flow:
            return SourceSearch(flow, provider=search_provider).inspect(search_id)

    @app.post("/api/searches/{search_id}/items/{position}/decision")
    def search_decision(search_id: str, position: int, payload: InboxDecision):
        with pipeline() as flow:
            return SourceSearch(flow, provider=search_provider).decide(
                search_id, position, payload.action, payload.submission_id
            )

    @app.get("/api/inbox")
    def inbox(state: str = "pending", q: str = "", freshness: str = "all", feed: str = ""):
        with pipeline() as flow:
            module = FeedInbox(flow)
            return {
                "items": module.list(state=state, q=q, freshness=freshness, feed=feed),
                "feeds": module.feeds(),
            }

    @app.post("/api/inbox/refresh")
    def refresh_feed(payload: NewRun):
        with pipeline() as flow:
            return FeedInbox(flow).refresh(payload.url)

    @app.post("/api/inbox/{item_id}/decision")
    def inbox_decision(item_id: int, payload: InboxDecision):
        with pipeline() as flow:
            return FeedInbox(flow).decide(item_id, payload.action, payload.submission_id)

    @app.get("/publication", response_class=HTMLResponse)
    def publication_page():
        return (frontend / "publication" / "index.html").read_text(encoding="utf-8")

    @app.get("/reading-builds", response_class=HTMLResponse)
    def reading_build_page():
        return (frontend / "reading-builds" / "index.html").read_text(encoding="utf-8")

    @app.get("/api/reading-builds")
    def reading_build_state():
        with pipeline() as flow:
            return ReadingBuilds(
                flow.store, runner=reading_runner, demo=review_origin == "demo"
            ).inspect()

    @app.post("/api/reading-builds")
    def reading_build(payload: BuildInput):
        with pipeline() as flow:
            return ReadingBuilds(
                flow.store, runner=reading_runner, demo=review_origin == "demo"
            ).build(payload)

    @app.get("/reading-preview/{build_id}/")
    @app.get("/reading-preview/{build_id}/{name:path}")
    def reading_artifact(build_id: str, name: str = ""):
        with pipeline() as flow:
            try:
                path = ReadingBuilds(flow.store, demo=review_origin == "demo").artifact(build_id, name)
            except (ValueError, OSError) as error:
                raise HTTPException(404, str(error)) from error
            return FileResponse(
                path,
                media_type="font/woff2" if path.suffix == ".woff2" else None,
                headers={
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; base-uri 'none'; form-action 'none'",
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                },
            )

    @app.get("/api/runs/{run_id}/publication")
    def publication_state(run_id: str):
        with pipeline() as flow:
            return Publication(flow.store, origin=review_origin).inspect(run_id)

    @app.post("/api/runs/{run_id}/publication/previews")
    def publication_preview(run_id: str, payload: PreviewInput):
        with pipeline() as flow:
            return Publication(flow.store, origin=review_origin).prepare(run_id, payload)

    @app.post("/api/runs/{run_id}/publication/previews/{preview_id}/approve")
    def publication_approval(run_id: str, preview_id: int, payload: ApprovalInput):
        with pipeline() as flow:
            return Publication(flow.store, origin=review_origin).approve(
                run_id, preview_id, payload
            )

    @app.post("/api/runs/{run_id}/publication/previews/{preview_id}/export")
    def publication_export(run_id: str, preview_id: int, mode: str = "preview"):
        with pipeline() as flow:
            body = Publication(flow.store, origin=review_origin).export(
                run_id, preview_id, mode=mode
            )
            return PlainTextResponse(
                body,
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="note-{preview_id}-{mode}.json"',
                    "X-Content-Type-Options": "nosniff",
                },
            )

    @app.get("/api/candidates")
    def candidates(q: str = "", state: str = "all", freshness: str = "all", origin: str = "all"):
        with pipeline() as flow:
            return Candidates(flow).list(q=q, state=state, freshness=freshness, origin=origin)

    @app.post("/api/candidates")
    def import_candidate(payload: ImportCandidate):
        with pipeline() as flow:
            return Candidates(flow).import_link(payload.model_dump(mode="json"))

    @app.post("/api/candidates/process")
    def submit_candidate(payload: ImportCandidate):
        with pipeline() as flow:
            return Candidates(flow, transport=translation_transport).submit(
                payload.model_dump(mode="json")
            )

    @app.post("/api/candidates/{candidate_id}/process")
    def process_candidate(candidate_id: str, payload: ScreeningRequest):
        with pipeline() as flow:
            return Candidates(flow, transport=translation_transport).process(
                candidate_id, payload.submission_id
            )

    @app.get("/api/candidates/{candidate_id}")
    def inspect_candidate(candidate_id: str):
        with pipeline() as flow:
            return Candidates(flow).inspect(candidate_id)

    @app.post("/api/candidates/{candidate_id}/capture")
    def capture_candidate(candidate_id: str):
        with pipeline() as flow:
            return Candidates(flow).capture(candidate_id)

    @app.post("/api/candidates/{candidate_id}/review")
    def review_candidate(candidate_id: str, payload: ScreeningRequest):
        with pipeline() as flow:
            return Candidates(flow, transport=translation_transport).review(
                candidate_id, payload.submission_id
            )

    @app.get("/api/library")
    def library():
        with pipeline() as flow:
            return Library(flow).list()

    @app.get("/api/library/{run_id}")
    def library_article(run_id: str, result_id: int | None = None):
        with pipeline() as flow:
            return Library(flow).article(run_id, result_id)

    @app.post("/api/library/{run_id}/edits")
    def edit_reading_copy(run_id: str, payload: EditInput):
        with pipeline() as flow:
            return ProcessingResults(flow.store).edit(run_id, payload.model_dump())

    @app.get("/api/runs/{run_id}/curation")
    def curation(run_id: str):
        with pipeline() as flow:
            return Curation(flow.store, origin=review_origin).inspect(run_id)

    @app.post("/api/runs/{run_id}/curation")
    def record_curation(run_id: str, payload: CurationInput):
        with pipeline() as flow:
            return Curation(flow.store, origin=review_origin).record(run_id, payload)

    @app.get("/api/library/{run_id}/note")
    def download_note(run_id: str):
        with pipeline() as flow:
            state = Curation(flow.store, origin=review_origin).inspect(run_id)
            if not state["available"]:
                raise HTTPException(400, "请先提取原文。")
            document = flow.store.version(state["version_id"])["document"]
            return PlainTextResponse(
                export_note(document, state),
                headers={
                    "Content-Disposition": 'attachment; filename="personal-note.md"',
                    "X-Content-Type-Options": "nosniff",
                },
            )

    @app.get("/curation.js")
    def curation_script():
        return PlainTextResponse(
            Path(__file__).with_name("curation.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/curation.css")
    def curation_css():
        return PlainTextResponse(
            Path(__file__).with_name("curation.css").read_text(encoding="utf-8"),
            media_type="text/css",
        )

    @app.get("/reading.css", response_class=PlainTextResponse)
    def reading_css():
        return PlainTextResponse(
            Path(__file__).with_name("reading.css").read_text(encoding="utf-8"),
            media_type="text/css",
        )

    @app.get("/assets/{name}", response_class=PlainTextResponse)
    def presentation_asset(name: str):
        media_types = {
            "ui.js": "application/javascript",
            "reader.js": "application/javascript",
            "record-view.js": "application/javascript",
            "record-view.css": "text/css",
            "workbench.css": "text/css",
        }
        if name not in media_types:
            raise HTTPException(404, "Asset not found")
        return PlainTextResponse(
            Path(__file__).with_name(name).read_text(encoding="utf-8"),
            media_type=media_types[name],
        )

    @app.get("/workspace.js", response_class=PlainTextResponse)
    def workspace_script():
        return PlainTextResponse(
            Path(__file__).with_name("workspace.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/archive", response_class=HTMLResponse)
    def archive():
        return Path(__file__).with_name("archive.html").read_text(encoding="utf-8")

    @app.get("/review.js", response_class=PlainTextResponse)
    def review_script():
        return PlainTextResponse(
            Path(__file__).with_name("review.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/comparison.js", response_class=PlainTextResponse)
    def comparison_script():
        return PlainTextResponse(
            Path(__file__).with_name("comparison.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/translation.js", response_class=PlainTextResponse)
    def translation_script():
        return PlainTextResponse(
            Path(__file__).with_name("translation.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    def translations(flow):
        return Translation(
            flow.store,
            transport=translation_transport,
            origin="demo" if review_origin == "demo" else "model_api",
        )

    @app.get("/processing.js", response_class=PlainTextResponse)
    def processing_script():
        return PlainTextResponse(
            Path(__file__).with_name("processing.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/api/runs/{run_id}/processing")
    def processing(run_id: str):
        with pipeline() as flow:
            return ProcessingResults(flow.store).inspect(run_id)

    @app.get("/api/runs/{run_id}/processing/task")
    def processing_task(run_id: str):
        with pipeline() as flow:
            task = ProcessingResults(flow.store).task(run_id)
            task["result_template"] = {
                "task_key": task["task_key"],
                "submission_id": "replace-with-unique-id",
                "origin": "external_ai",
                "executor": "填写实际执行工具",
                "model": None,
                "execution_note": "填写实际处理方式；未观察到的内部日志保持未知。",
                "segments": [
                    {"index": p["index"], "text": p["source"] if p["kind"] == "code" else ""}
                    for p in task["segments"]
                ],
            }
            return PlainTextResponse(
                json.dumps(task, ensure_ascii=False, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="reading-task.json"'},
            )

    @app.post("/api/runs/{run_id}/processing/results")
    def receive_result(run_id: str, payload: ResultInput):
        with pipeline() as flow:
            return ProcessingResults(flow.store).receive(run_id, payload.model_dump())

    @app.post("/api/runs/{run_id}/processing/translations/{translation_id}")
    def receive_translation(run_id: str, translation_id: int):
        with pipeline() as flow:
            return ProcessingResults(flow.store).receive(run_id, translation_id=translation_id)

    @app.post("/api/runs/{run_id}/processing/results/{result_id}/reviews")
    def review_result(run_id: str, result_id: int, payload: ResultReview):
        with pipeline() as flow:
            return ProcessingResults(flow.store, review_origin=review_origin).review(
                run_id, result_id, payload.model_dump()
            )

    @app.get("/api/runs/{run_id}/translation")
    def translation(run_id: str, translation_id: int | None = None, detail: Literal["summary", "full"] = "full"):
        with pipeline() as flow:
            return translations(flow).inspect(run_id, translation_id, detail=detail)

    @app.get("/api/runs/{run_id}/translation/plan")
    def translation_plan(run_id: str):
        with pipeline() as flow:
            return translations(flow).plan(run_id)

    @app.get("/api/runs/{run_id}/translation/attempts/{attempt_id}")
    def translation_attempt(run_id: str, attempt_id: int):
        with pipeline() as flow:
            return translations(flow).attempt(run_id, attempt_id)

    def execute_translation(identifier):
        with pipeline() as flow:
            translations(flow).execute(identifier)

    @app.post("/api/runs/{run_id}/translation/start", status_code=202)
    def start_translation(run_id: str, payload: ScreeningRequest, tasks: BackgroundTasks):
        with pipeline() as flow:
            claimed = translations(flow).begin(run_id, payload.submission_id)
        if claimed["launch"]:
            tasks.add_task(execute_translation, claimed["id"])
        return claimed

    @app.post("/api/runs/{run_id}/translation/next")
    def translate_next(run_id: str):
        with pipeline() as flow:
            state = translations(flow).advance(run_id)
            if state["status"] == "succeeded":
                # Register the observed result, without a second model call or human approval.
                ProcessingResults(flow.store).receive(run_id, translation_id=state["id"])
            return state

    @app.get("/api/runs/{run_id}/translation/text", response_class=PlainTextResponse)
    def translated_text(run_id: str):
        with pipeline() as flow:
            result = translations(flow).inspect(run_id)
            if result["text"] is None:
                raise HTTPException(409, "全文尚未翻译完成，请在对照视图查看已完成部分。")
            return PlainTextResponse(result["text"], headers={"X-Content-Type-Options": "nosniff"})

    def require_active_run(flow, run_id):
        report = Comparison(flow.store).get()
        if report and any(
            m.get("experiment_id") == run_id and m["status"] == "archived"
            for m in report["methods"]
        ):
            raise HTTPException(409, "此试跑已归档，保留结果供审计，不再继续执行。")

    @app.get("/api/runs")
    def runs():
        with pipeline() as flow:
            return flow.list()

    @app.get("/api/experiments")
    def experiments():
        with pipeline() as flow:
            return DiscoveryExperiment(flow).list()

    @app.get("/api/comparisons/latest")
    def latest_comparison():
        with pipeline() as flow:
            return Comparison(flow.store).get()

    @app.get("/api/comparisons/{report_id}")
    def comparison(report_id: str):
        with pipeline() as flow:
            return Comparison(flow.store).get(report_id)

    @app.get("/api/comparisons/{report_id}/methods/{method_id}")
    def method_archive(report_id: str, method_id: str):
        with pipeline() as flow:
            report = Comparison(flow.store).get(report_id)
            if method_id not in {m["id"] for m in report["methods"]}:
                raise HTTPException(404, "Unknown method")
            path = (
                flow.store.root / "comparisons" / report["as_of"] / method_id / "result.json"
            ).resolve()
            if not path.is_relative_to(flow.store.root.resolve()) or not path.is_file():
                raise HTTPException(404, "Archive unavailable")
            return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/comparisons/{report_id}/feedback/{source_id}")
    def comparison_feedback(report_id: str, source_id: str, payload: Feedback):
        with pipeline() as flow:
            return Comparison(flow.store).feedback(report_id, source_id, payload.model_dump())

    @app.post("/api/experiments")
    def new_experiment(payload: SearchPlan):
        with pipeline() as flow:
            active = Comparison(flow.store).get()
            if (
                active
                and active["selected_method"] != "exa_fixed"
                and payload.search_provider == "exa_mcp"
            ):
                raise HTTPException(409, "Exa 已归档为对照；当前保留 Codex 目的驱动搜集。")
            return DiscoveryExperiment(flow).create(payload.model_dump(mode="json"))

    @app.get("/api/experiments/{run_id}")
    def experiment(run_id: str):
        with pipeline() as flow:
            return DiscoveryExperiment(flow).inspect(run_id)

    @app.post("/api/experiments/{run_id}/next-search")
    def next_search(run_id: str):
        with pipeline() as flow:
            require_active_run(flow, run_id)
            return DiscoveryExperiment(flow).next_search(run_id)

    @app.post("/api/experiments/{run_id}/searches/{event_id}")
    def import_search(run_id: str, event_id: str, payload: SearchObservation):
        with pipeline() as flow:
            require_active_run(flow, run_id)
            return DiscoveryExperiment(flow).import_search(
                run_id, event_id, payload.model_dump(mode="json")
            )

    @app.post("/api/experiments/{run_id}/search-next")
    def search_next(run_id: str):
        with pipeline() as flow:
            active = Comparison(flow.store).get()
            if active and active["selected_method"] != "exa_fixed":
                raise HTTPException(409, "此方式已归档；历史结果可查看，当前不再执行。")
            return DiscoveryExperiment(flow).search_next(run_id, exa_search)

    @app.post("/api/experiments/{run_id}/capture-next")
    def capture_next(run_id: str):
        with pipeline() as flow:
            require_active_run(flow, run_id)
            return DiscoveryExperiment(flow).capture_next(run_id)

    @app.get("/api/discovery")
    def discoveries():
        with pipeline() as flow:
            return Discovery(flow.store).list()

    @app.post("/api/discovery/prepare")
    def discovery_input(payload: DiscoveryGoal):
        with pipeline():
            return Discovery.prepare(payload.goal, payload.as_of, payload.max_age_days)

    @app.post("/api/discovery")
    def import_discovery(payload: SelectionBatch):
        with pipeline() as flow:
            return Discovery(flow.store).accept(payload.model_dump(mode="json"))

    @app.post("/api/runs")
    def create(payload: NewRun):
        with pipeline() as flow:
            return flow.create(payload.url)

    @app.get("/api/runs/{run_id}")
    def state(run_id: str):
        with pipeline() as flow:
            return flow.inspect(run_id)

    @app.post("/api/runs/{run_id}/reextract")
    def reextract(run_id: str):
        with pipeline() as flow:
            return flow.reextract(run_id)

    @app.get("/api/runs/{run_id}/reviews")
    def reviews(run_id: str):
        with pipeline() as flow:
            return flow.reviews.inspect(run_id)

    @app.post("/api/runs/{run_id}/reviews")
    def record_review(run_id: str, payload: ReviewInput):
        with pipeline() as flow:
            return flow.reviews.record(run_id, payload)

    @app.get("/api/runs/{run_id}/stages/{stage}")
    def stage_output(run_id: str, stage: str):
        with pipeline() as flow:
            for item in flow.inspect(run_id)["stages"]:
                if item["id"] == stage:
                    return item
            raise HTTPException(404, "Unknown stage")

    @app.post("/api/runs/{run_id}/stages/{stage}")
    def execute(run_id: str, stage: str):
        with pipeline() as flow:
            return flow.run(run_id, stage)

    @app.get("/api/runs/{run_id}/artifacts/{name}", response_class=PlainTextResponse)
    def artifact(run_id: str, name: str):
        with pipeline() as flow:
            return PlainTextResponse(
                flow.artifact(run_id, name), headers={"X-Content-Type-Options": "nosniff"}
            )

    return app


app = create_app()
