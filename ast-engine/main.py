import hmac
import json
import logging
import os
import re
import threading
import time
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from analyzer import analyze_code_structure

log = logging.getLogger("devdna.ast-engine")

app = FastAPI(title="DevDNA AST Engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "AST_ENGINE_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Opt-in token auth, same env-var pattern as the backend: when
# AST_ENGINE_API_TOKEN is set, /analyze/repo requires a matching
# X-DevDNA-Token header (compared with hmac.compare_digest). The health
# endpoint stays open so status checks keep working.
TOKEN_HEADER = "X-DevDNA-Token"
_RATE_LIMIT = int(os.environ.get("AST_ENGINE_RATE_LIMIT_PER_MINUTE", "60"))
_WINDOW_S = 60.0
_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


def _allow(ip: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        bucket = [t for t in _rate_hits.get(ip, []) if now - t < _WINDOW_S]
        if len(bucket) >= _RATE_LIMIT:
            _rate_hits[ip] = bucket
            return False
        bucket.append(now)
        _rate_hits[ip] = bucket
        return True


def _reset_rate_limit() -> None:
    with _rate_lock:
        _rate_hits.clear()


class _GuardMiddleware:
    """Per-IP rate limit on every route + opt-in token auth on /analyze/repo."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            client = scope.get("client")
            ip = client[0] if client and client[0] else "unknown"
            if not _allow(ip):
                log.warning("429 rate_limited %s %s", ip, path)
                await _json(send, 429, "Too many requests. Slow down and retry in a minute.")
                return
            token = os.environ.get("AST_ENGINE_API_TOKEN", "").strip()
            if token and path.rstrip("/") != "/":
                sent = {k.decode("latin-1").lower(): v.decode("latin-1")
                        for k, v in scope.get("headers", [])}
                supplied = sent.get(TOKEN_HEADER.lower(), "")
                if not supplied or not hmac.compare_digest(supplied, token):
                    log.warning("401 unauthorized %s %s", ip, path)
                    await _json(send, 401, f"Missing or invalid {TOKEN_HEADER} header.")
                    return
        await self.app(scope, receive, send)


async def _json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


app.add_middleware(_GuardMiddleware)


def _get(url: str, headers: dict, timeout: int = 30) -> requests.Response:
    """requests.get with a mandatory timeout; GitHub failures surface with
    distinct statuses instead of a generic 404."""
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.Timeout:
        log.warning("GitHub request timed out: %s", url)
        raise HTTPException(status_code=504,
                            detail="GitHub did not respond in time. Try again later.") from None
    except requests.RequestException as exc:
        log.warning("GitHub request failed: %s (%s)", url, exc)
        raise HTTPException(status_code=502,
                            detail="Could not reach GitHub. Try again later.") from None
    if response.status_code == 403:
        log.warning("GitHub rate limited while fetching %s", url)
        raise HTTPException(status_code=429,
                            detail="GitHub API rate limit exceeded for this host. Try again later.")
    if response.status_code == 401:
        raise HTTPException(status_code=502,
                            detail="GitHub rejected the request (authentication). Is the repository private?")
    if response.status_code >= 500:
        raise HTTPException(status_code=502,
                            detail=f"GitHub returned an error ({response.status_code}) while fetching the repository.")
    return response

@app.get("/")
def read_root():
    return {"status": "DevDNA AST Engine is running. Go to /docs to test."}


def _repository_from_github_url(github_url: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(github_url.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="Enter a valid GitHub repository URL.") from None

    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or len(parts) != 2
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status_code=422,
            detail="Enter a repository URL in the form https://github.com/owner/repository.",
        )

    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if (
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo)
    ):
        raise HTTPException(status_code=422, detail="The GitHub owner or repository name is invalid.")
    return owner, repo


@app.get("/analyze/repo")
def analyze_entire_repo(
    owner: str | None = None,
    repo: str | None = None,
    branch: str = "main",
    github_url: str | None = None,
):
    if github_url:
        if owner is not None or repo is not None:
            raise HTTPException(
                status_code=422,
                detail="Provide github_url or owner and repo, not both.",
            )
        owner, repo = _repository_from_github_url(github_url)
    elif not owner or not repo:
        raise HTTPException(
            status_code=422,
            detail="Provide github_url or both owner and repo.",
        )

    if not branch.strip():
        raise HTTPException(status_code=422, detail="Branch must not be empty.")

    # 1. Fetch the entire file tree recursively from GitHub API
    tree_url = (
        f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        f"/git/trees/{quote(branch, safe='/')}?recursive=1"
    )
    headers = {"User-Agent": "DevDNA-AST-Engine"}

    response = _get(tree_url, headers, timeout=30)
    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail=f"Repository or branch '{branch}' not found on GitHub."
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub returned an error ({response.status_code}) while fetching the repository tree."
        )

    tree_data = response.json().get("tree", [])
    
    # 2. Filter out only Python source files (ignoring migrations, venvs, tests if needed)
    py_files = [
        item["path"] for item in tree_data 
        if item["type"] == "blob" and item["path"].endswith(".py")
    ]

    if not py_files:
        return {
            "repository": f"{owner}/{repo}",
            "file_count": 0,
            "message": "No Python files found in this repository."
        }

    # 3. Analyze each file (limit to top 30 files for performance during MVP)
    file_results = []
    total_score = 0.0
    analyzed_count = 0

    for filepath in py_files[:30]:
        raw_url = (
            f"https://raw.githubusercontent.com/{quote(owner, safe='')}/{quote(repo, safe='')}"
            f"/{quote(branch, safe='/')}/{quote(filepath, safe='/')}"
        )
        raw_res = _get(raw_url, headers, timeout=20)
        if raw_res.status_code == 200:
            analysis = analyze_code_structure(raw_res.text)
            file_results.append({
                "file": filepath,
                "score": analysis["human_architect_score"],
                "stats": analysis["ast_stats"]
            })
            total_score += analysis["human_architect_score"]
            analyzed_count += 1

    # 4. Calculate overall Repository Architectural Authenticity
    avg_score = round(total_score / analyzed_count, 2) if analyzed_count > 0 else 0

    return {
        "repository": f"{owner}/{repo}",
        "branch": branch,
        "total_python_files": len(py_files),
        "analyzed_files": analyzed_count,
        "overall_architect_score": avg_score,
        # Complexity heuristic (functions/classes/branches/depth), not AI
        # detection: below the threshold means "review manually".
        "is_ai_flagged": avg_score < 35.0,
        "file_breakdown": file_results
    }