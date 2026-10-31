# Contributing to DevDNA

Thank you for your interest in contributing to DevDNA! We welcome bug fixes, documentation improvements, new analysis rules, and feature suggestions.

---

## 1. Development Prerequisites

* **Python:** 3.11 or later
* **Node.js:** 18 or later (npm 9+)
* **Docker (Optional):** Required only if developing features for the containerized What-If sandbox.

---

## 2. Local Setup Guide

### Backend (FastAPI + Python AST)
```bash
cd backend
python -m venv .venv

# Activate the virtual environment
# Windows (PowerShell):
.\.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Initialize environment configuration
copy .env.example .env    # Windows
# cp .env.example .env    # macOS/Linux

# Start development API server
uvicorn app.main:app --reload --port 8000
```

### Frontend (React 18 + Vite)
```bash
cd frontend
npm install

# Start Vite development server
npm run dev
```

Open `http://localhost:5173` in your browser.

---

## 3. Running Validation & Tests

Before submitting any Pull Request, ensure all automated tests pass cleanly without errors or warnings:

### Backend Tests
```bash
cd backend
python -m pytest -v
```
*(All 139 unit and integration tests must pass).*

### Frontend Tests & Production Build
```bash
cd frontend
npm test -- --run
npm run build
```
*(All 22 component and utility tests must pass, and the Vite production build must succeed).*

---

## 4. Coding Standards

* **Determinism First:** Core analysis and failure propagation logic must remain 100% deterministic. Never introduce non-deterministic heuristics or mock data into core graph algorithms.
* **Security & Redaction:** Any code handling file paths or user input must strictly respect path traversal protections (`zip_safety.py`) and secret redaction (`redact.py`).
* **Frontend Design System:** Follow the established modern design tokens in `index.css`. Ensure interactive components work seamlessly across both Dark and Bright modes with accessible contrast ratios.
* **No Broken Commits:** Every commit should leave the test suite green. Use clear, conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).
