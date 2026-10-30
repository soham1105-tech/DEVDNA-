"""Project ingestion (sample, ZIP upload, explicitly allowed local path) and analysis runs."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyzer.ingest import ZipSecurityError, safe_extract_zip, scan_tree
from app.api.deps import get_project
from app.config import settings
from app.db import get_db
from app.models import (
    AIInteraction, AnalysisRun, Experiment, FindingSuppression, FindingSuppressionAudit, Prediction, Project,
    ReportRecord, _id,
)
from app.schemas import GitHubProjectIn, LocalProjectIn
from app.services.analysis import run_analysis
from app.services.serialize import project_out, run_out

router = APIRouter(prefix="/api", tags=["projects"])


def _start_run(db: Session, project: Project, background: BackgroundTasks) -> AnalysisRun:
    run = AnalysisRun(project_id=project.id)
    db.add(run)
    db.commit()
    background.add_task(run_analysis, run.id)
    return run


def _github_repository_fields(github_url: str | None, github_branch: str | None) -> dict[str, str]:
    url = (github_url or "").strip()
    branch = (github_branch or "").strip()
    if not url and not branch:
        return {}
    if not url:
        raise HTTPException(422, detail={"code": "invalid_github_repository",
                                         "message": "Enter a GitHub repository URL when specifying a branch."})

    if len(url) > 500:
        raise HTTPException(422, detail={"code": "invalid_github_repository",
                                         "message": "The GitHub repository URL must be 500 characters or fewer."})
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise HTTPException(422, detail={"code": "invalid_github_repository",
                                         "message": "Enter a valid GitHub repository URL."}) from None
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or len(parts) != 2
            or parsed.query or parsed.fragment):
        raise HTTPException(422, detail={"code": "invalid_github_repository",
                                         "message": "Enter a repository URL in the form https://github.com/owner/repository."})
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if (not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo)):
        raise HTTPException(422, detail={"code": "invalid_github_repository",
                                         "message": "The GitHub owner or repository name is invalid."})
    if len(branch) > 255:
        raise HTTPException(422, detail={"code": "invalid_github_branch",
                                         "message": "The GitHub branch name must be 255 characters or fewer."})
    return {"ast_owner": owner, "ast_repo": repo, "ast_branch": branch or "main"}


def _download_github_archive(owner: str, repo: str, branch: str) -> bytes:
    url = (
        f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        f"/zipball/{quote(branch, safe='/')}"
    )
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=True) as client:
            with client.stream("GET", url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "DevDNA"}) as response:
                if response.status_code == 404:
                    raise HTTPException(404, detail={"code": "github_not_found",
                                                     "message": "GitHub repository or branch was not found. Check the URL and branch."})
                if response.status_code == 403:
                    raise HTTPException(502, detail={"code": "github_rate_limited",
                                                     "message": "GitHub denied the archive request, possibly due to rate limiting. Try again later."})
                if response.status_code != 200:
                    raise HTTPException(502, detail={"code": "github_download_failed",
                                                     "message": f"GitHub archive download failed ({response.status_code})."})

                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > settings.max_upload_bytes:
                    raise HTTPException(413, detail={"code": "too_large",
                                                     "message": f"GitHub archive exceeds {settings.max_upload_bytes // (1024 * 1024)} MB."})

                archive = bytearray()
                for chunk in response.iter_bytes():
                    archive.extend(chunk)
                    if len(archive) > settings.max_upload_bytes:
                        raise HTTPException(413, detail={"code": "too_large",
                                                         "message": f"GitHub archive exceeds {settings.max_upload_bytes // (1024 * 1024)} MB."})
                return bytes(archive)
    except httpx.TimeoutException:
        raise HTTPException(504, detail={"code": "github_timeout",
                                         "message": "Timed out while downloading the GitHub archive. Try again."}) from None
    except httpx.RequestError:
        raise HTTPException(502, detail={"code": "github_unreachable",
                                         "message": "Could not reach GitHub to download the repository."}) from None


def _create_zip_project(
    data: bytes,
    name: str,
    source_type: str,
    ast_repository: dict[str, str],
    db: Session,
    background: BackgroundTasks,
) -> dict:
    pid = _id()
    project_dir = settings.projects_dir / pid
    dest = project_dir / "source"
    try:
        stats = safe_extract_zip(data, dest)
    except ZipSecurityError as exc:
        shutil.rmtree(project_dir, ignore_errors=True)
        raise HTTPException(400, detail={"code": "unsafe_archive", "message": str(exc)}) from exc

    scan = scan_tree(dest)
    if not any(path.endswith(".py") for path in scan["files"]):
        shutil.rmtree(project_dir, ignore_errors=True)
        raise HTTPException(422, detail={"code": "no_python",
                                         "message": "No Python source files found. DevDNA currently analyses Python projects."})
    stats.pop("sensitive_files", None)
    project = Project(id=pid, name=name[:200], source_type=source_type, source_path=str(dest), trusted=False,
                      **ast_repository,
                      ingest_stats={**stats, "files": scan["file_count"], "languages": scan["languages"]})
    db.add(project)
    db.commit()
    run = _start_run(db, project, background)
    return {"project": project_out(project), "run": run_out(run)}


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    return [project_out(p, db.get(AnalysisRun, p.latest_run_id) if p.latest_run_id else None) for p in projects]


@router.post("/projects/sample", status_code=201)
def load_sample(background: BackgroundTasks, db: Session = Depends(get_db)):
    """Copy the bundled ShopLite sample into a fresh project and start analysis."""
    pid = _id()
    dest = settings.projects_dir / pid / "source"
    shutil.copytree(settings.sample_project_dir, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.db"))
    scan = scan_tree(dest)
    project = Project(id=pid, name="ShopLite (demo)", source_type="sample", source_path=str(dest), trusted=True,
                      ingest_stats={"files": scan["file_count"], "languages": scan["languages"]})
    db.add(project)
    db.commit()
    run = _start_run(db, project, background)
    return {"project": project_out(project), "run": run_out(run)}


@router.post("/projects/upload", status_code=201)
async def upload_project(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    github_url: str | None = Form(default=None),
    github_branch: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    ast_repository = _github_repository_fields(github_url, github_branch)
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, detail={"code": "invalid_file", "message": "Upload a .zip archive of the repository."})
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, detail={"code": "too_large",
                                         "message": f"Upload exceeds {settings.max_upload_bytes // (1024 * 1024)} MB."})
    name = Path(file.filename).stem[:200] or "Uploaded project"
    return _create_zip_project(data, name, "upload", ast_repository, db, background)


@router.post("/projects/github", status_code=201)
def github_project(body: GitHubProjectIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Download a public GitHub repository archive and start static analysis."""
    ast_repository = _github_repository_fields(body.github_url, body.github_branch)
    archive = _download_github_archive(
        ast_repository["ast_owner"], ast_repository["ast_repo"], ast_repository["ast_branch"],
    )
    return _create_zip_project(
        archive, ast_repository["ast_repo"], "github", ast_repository, db, background,
    )


@router.post("/projects/local", status_code=201)
def local_project(body: LocalProjectIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Analyse a folder on the server. Only allowed inside DEVDNA_ALLOWED_LOCAL_ROOTS (read-only analysis)."""
    if not settings.allowed_local_roots:
        raise HTTPException(403, detail={"code": "local_disabled",
                                         "message": "Local repository analysis is disabled. Set DEVDNA_ALLOWED_LOCAL_ROOTS in backend/.env."})
    path = Path(body.path).expanduser().resolve()
    roots = [Path(r).expanduser().resolve() for r in settings.allowed_local_roots]
    if not any(path == r or r in path.parents for r in roots):
        raise HTTPException(403, detail={"code": "path_not_allowed", "message": "That path is outside the allowed local roots."})
    if not path.is_dir():
        raise HTTPException(404, detail={"code": "not_found", "message": f"Directory not found: '{body.path}'"})
    scan = scan_tree(path)
    if not any(f.endswith(".py") for f in scan["files"]):
        raise HTTPException(422, detail={"code": "no_python", "message": "No Python source files found. DevDNA currently analyses Python projects."})
    project = Project(name=body.name or path.name, source_type="local", source_path=str(path), trusted=False,
                      **_github_repository_fields(body.github_url, body.github_branch),
                      ingest_stats={"files": scan["file_count"], "languages": scan["languages"]})
    db.add(project)
    db.commit()
    run = _start_run(db, project, background)
    return {"project": project_out(project), "run": run_out(run)}


@router.get("/projects/{project_id}")
def project_detail(project_id: str, db: Session = Depends(get_db)):
    p = get_project(db, project_id)
    return project_out(p, db.get(AnalysisRun, p.latest_run_id) if p.latest_run_id else None)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db)):
    p = get_project(db, project_id)
    if p.source_type in ("sample", "upload", "github"):
        shutil.rmtree(settings.projects_dir / p.id, ignore_errors=True)
    for model in (Experiment, Prediction, AIInteraction, ReportRecord, FindingSuppression, FindingSuppressionAudit):
        db.query(model).filter(model.project_id == p.id).delete()
    db.delete(p)
    db.commit()


@router.post("/projects/{project_id}/analyze", status_code=202)
def analyze(project_id: str, background: BackgroundTasks, db: Session = Depends(get_db)):
    p = get_project(db, project_id)
    running = db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == p.id,
                                                   AnalysisRun.status.in_(["queued", "running"]))).first()
    if running:
        return run_out(running)
    return run_out(_start_run(db, p, background))


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    r = db.get(AnalysisRun, run_id)
    if not r:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Analysis run not found"})
    return run_out(r)
