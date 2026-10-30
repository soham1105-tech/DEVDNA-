import os
import re
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from analyzer import analyze_code_structure

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
    
    response = requests.get(tree_url, headers=headers)
    if response.status_code != 200:
        raise HTTPException(
            status_code=404, 
            detail=f"Repository or branch '{branch}' not found on GitHub."
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
        raw_res = requests.get(raw_url)
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