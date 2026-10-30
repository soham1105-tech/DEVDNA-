import difflib
import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import settings
from app.db import SessionLocal
from app.experiments import runner
from app.models import Experiment, Prediction, Project


SAMPLE_PROJECT_DIR = Path(__file__).resolve().parent.parent / "sample_project"


def _is_valid_unified_diff(diff_text: str) -> bool:
    """Validate that the diff has standard unified diff structure."""
    lines = diff_text.strip().splitlines()
    has_from_header = any(line.startswith("--- ") for line in lines)
    has_to_header = any(line.startswith("+++ ") for line in lines)
    has_hunk_header = any(re.match(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", line) for line in lines)
    has_diff_content = any(line.startswith(("+", "-", " ")) for line in lines)
    return has_from_header and has_to_header and has_hunk_header and has_diff_content


def test_discrepancy_preconditions_and_preview(client, sample_project):
    # 1. Non-existent prediction -> 404
    res = client.post("/api/predictions/pred_does_not_exist/ai/discrepancy", json={"preview": True})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "prediction_not_found"

    # 2. Prediction without experiment -> 409 no_experiment_comparison
    pred_res = client.post(
        f"/api/projects/{sample_project}/predictions",
        json={"scenario": "external_api_unavailable", "target_id": "external:SHIPPING_API_URL"},
    )
    assert pred_res.status_code == 201
    pred_id = pred_res.json()["id"]

    res = client.post(f"/api/predictions/{pred_id}/ai/discrepancy", json={"preview": True})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "no_experiment_comparison"

    # 3. Experiment with status "prediction_confirmed" -> 409 invalid_status
    db = SessionLocal()
    try:
        confirmed_exp = Experiment(
            project_id=sample_project,
            prediction_id=pred_id,
            status="prediction_confirmed",
            comparison={"status": "prediction_confirmed", "rows": []},
        )
        db.add(confirmed_exp)
        db.commit()
        exp_id = confirmed_exp.id
    finally:
        db.close()

    res = client.post(f"/api/predictions/{pred_id}/ai/discrepancy", json={"preview": True})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "invalid_status"
    assert "partially_confirmed" in res.json()["error"]["message"]

    # 4. Experiment with status "partially_confirmed" -> preview returns evidence package
    db = SessionLocal()
    try:
        exp = db.get(Experiment, exp_id)
        exp.status = "partially_confirmed"
        exp.comparison = {
            "status": "partially_confirmed",
            "reason": "1 false positive observed",
            "metrics": {"false_positive": 1},
            "rows": [
                {
                    "nodeid": "tests/test_api.py::test_order_validation",
                    "verdict": "mismatch",
                    "prediction": "fail",
                    "observed": "passed",
                    "path": ["test:tests/test_api.py::test_order_validation", "route:POST /api/orders"],
                }
            ],
        }
        db.commit()
    finally:
        db.close()

    res = client.post(f"/api/predictions/{pred_id}/ai/discrepancy", json={"preview": True})
    assert res.status_code == 200
    data = res.json()
    assert data["preview"] is True
    assert "evidence" in data and len(data["evidence"]["items"]) > 0
    item_refs = [i["ref"] for i in data["evidence"]["items"]]
    assert all(r.startswith("E") for r in item_refs)

    # 5. Consent required check
    res = client.post(f"/api/predictions/{pred_id}/ai/discrepancy", json={})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "consent_required"

    # 6. Unconfigured check
    res = client.post(f"/api/predictions/{pred_id}/ai/discrepancy", json={"consent": True})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "not_configured"


def test_sample_project_shipping_api_discrepancy_verification(client, sample_project, monkeypatch):
    """End-to-end verification on the sample project's partially-confirmed shipping API case:
    1. Confirm the explanation cites real evidence from the package and strips ungrounded refs.
    2. Confirm the generated test fails under the fault and passes without it.
    3. Confirm the patch is a valid diff.
    4. Confirm never writes to user repo.
    """
    # 1. Create prediction for shipping API failure
    pred_res = client.post(
        f"/api/projects/{sample_project}/predictions",
        json={"scenario": "external_api_unavailable", "target_id": "external:SHIPPING_API_URL"},
    )
    assert pred_res.status_code == 201
    pred = pred_res.json()
    pred_id = pred["id"]

    # 2. Run real experiment on sample project (trusted -> local_harness)
    exp_res = client.post(f"/api/predictions/{pred_id}/experiments", json={"approved": True})
    assert exp_res.status_code == 202
    exp_id = exp_res.json()["id"]

    # Execute experiment synchronously
    from app.api.experiments import run_experiment
    run_experiment(exp_id)

    db = SessionLocal()
    try:
        exp = db.get(Experiment, exp_id)
        assert exp is not None
        assert exp.status == "partially_confirmed", f"Expected partially_confirmed, got {exp.status}"
        assert exp.comparison["metrics"]["false_positive"] >= 1
    finally:
        db.close()

    # Read mtime of all files in SAMPLE_PROJECT_DIR before discrepancy call
    source_files_before = {p: p.stat().st_mtime_ns for p in SAMPLE_PROJECT_DIR.rglob("*") if p.is_file()}

    # Check evidence package preview
    preview_res = client.post(f"/api/predictions/{pred_id}/ai/discrepancy", json={"preview": True})
    assert preview_res.status_code == 200
    preview_data = preview_res.json()
    allowed_refs = {i["ref"] for i in preview_data["evidence"]["items"]}
    ref_by_kind = {i["kind"]: i["ref"] for i in preview_data["evidence"]["items"]}

    # Prepare realistic Gemma output referencing actual evidence refs
    mismatched_ref = ref_by_kind.get("mismatched_test", "E3")
    route_ref = ref_by_kind.get("affected_route", "E5")

    valid_diff = (
        "--- a/shoplite/services/orders.py\n"
        "+++ b/shoplite/services/orders.py\n"
        "@@ -20,3 +20,7 @@\n"
        "     total = compute_total(product[\"price_cents\"], quantity, discount_code)\n"
        "-    shipping = ShippingClient().get_quote(product[\"weight_grams\"] * quantity)\n"
        "+    try:\n"
        "+        shipping = ShippingClient().get_quote(product[\"weight_grams\"] * quantity)\n"
        "+    except Exception:\n"
        "+        shipping = 500  # Fallback flat rate when shipping API is unavailable\n"
        "     order_id = insert_order(product_id, quantity, total, shipping)\n"
    )

    generated_test_code = (
        "def test_order_creation_fails_under_shipping_fault(client):\n"
        "    response = client.post('/api/orders', json={'product_id': 1, 'quantity': 1})\n"
        "    assert response.status_code == 201\n"
    )

    simulated_reply = json.dumps(
        {
            "explanation": (
                f"The deterministic prediction anticipated that all tests exercising routes dependent on "
                f"external:SHIPPING_API_URL would fail. However, test_order_validation [{mismatched_ref}] "
                f"passed because input validation (quantity: 0) rejected the request with HTTP 422 before "
                f"execution reached the shipping client on route POST /api/orders [{route_ref}]. "
                f"Ungrounded claim reference [E999] was also mentioned."
            ),
            "generated_test": generated_test_code,
            "suggested_patch": valid_diff,
            "evidence_used": [mismatched_ref, route_ref, "E999"],
        }
    )

    # Monkeypatch gemma to simulate successful generation with key configured
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    from app.ai.gemma import gemma
    monkeypatch.setattr(gemma, "_call", lambda prompt: simulated_reply)

    # 3. Call discrepancy endpoint
    discrepancy_res = client.post(f"/api/predictions/{pred_id}/ai/discrepancy", json={"consent": True})
    assert discrepancy_res.status_code == 200, discrepancy_res.text
    out = discrepancy_res.json()

    # --- Verification 1: Explanation cites real evidence and ungrounded refs are stripped ---
    assert "explanation" in out and len(out["explanation"]) > 0
    assert mismatched_ref in out["explanation"]
    assert "E999" not in out["explanation"], "Ungrounded ref E999 must be stripped from explanation text"
    assert "E999" not in out["evidence_used"], "Ungrounded ref E999 must be stripped from evidence_used"
    assert mismatched_ref in out["evidence_used"]
    assert all(ref in allowed_refs for ref in out["evidence_used"]), "All cited evidence_used must be grounded"

    # --- Verification 2: Suggested patch is a valid diff ---
    patch_text = out["suggested_patch"]
    assert _is_valid_unified_diff(patch_text), f"Patch is not a valid diff:\n{patch_text}"

    # --- Verification 3: Generated test fails under the fault and passes without it ---
    generated_test = out["generated_test"]
    assert "def " in generated_test and "assert " in generated_test

    fspec = {"kind": "external_api_unavailable", "hint": "SHIPPING_API_URL"}
    with tempfile.TemporaryDirectory() as td:
        proj_copy = Path(td) / "sample_project"
        shutil.copytree(SAMPLE_PROJECT_DIR, proj_copy)
        (proj_copy / "tests" / "test_generated_discrepancy.py").write_text(generated_test, encoding="utf-8")

        work_baseline = Path(td) / "w_baseline"
        work_baseline.mkdir()
        baseline_res = runner._run_pytest(proj_copy, work_baseline, None, "baseline", "local_harness")

        work_faulted = Path(td) / "w_faulted"
        work_faulted.mkdir()
        faulted_res = runner._run_pytest(proj_copy, work_faulted, fspec, "faulted", "local_harness")

        baseline_tests = runner.parse_junit(work_baseline / "baseline.xml")
        faulted_tests = runner.parse_junit(work_faulted / "faulted.xml")

        gen_baseline_outcome = [v["outcome"] for k, v in baseline_tests.items() if "test_order_creation_fails" in k]
        gen_faulted_outcome = [v["outcome"] for k, v in faulted_tests.items() if "test_order_creation_fails" in k]

        assert gen_baseline_outcome == ["passed"], f"Generated test must pass without fault: {baseline_tests}"
        assert gen_faulted_outcome in (["failed"], ["error"]), f"Generated test must fail under fault: {faulted_tests}"

    # --- Verification 4: Never write to the user's repo, never auto-merge, never auto-apply the patch ---
    source_files_after = {p: p.stat().st_mtime_ns for p in SAMPLE_PROJECT_DIR.rglob("*") if p.is_file()}
    assert source_files_before == source_files_after, "Files in user's repository were modified!"

    # Verify latest discrepancy endpoint works
    latest_res = client.get(f"/api/predictions/{pred_id}/ai/discrepancy/latest")
    assert latest_res.status_code == 200
    latest_data = latest_res.json()
    assert latest_data["explanation"] == out["explanation"]
    assert latest_data["generated_test"] == out["generated_test"]
    assert latest_data["suggested_patch"] == out["suggested_patch"]
