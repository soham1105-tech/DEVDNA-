import json
import subprocess
import sys
from pathlib import Path

from app.mcp_server import handle_message

BACKEND = Path(__file__).resolve().parent.parent
SAMPLE = BACKEND / "sample_project"


def _tool_call(name: str, arguments: dict) -> dict:
    response = handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert response is not None
    result = response["result"]
    assert result["isError"] is False, result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


def test_tools_list_returns_both_tools():
    response = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert response is not None
    tools = response["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"devdna_blast_radius", "devdna_repo_summary"}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert "repo_path" in tool["inputSchema"]["required"]


def test_repo_summary_has_routes():
    summary = _tool_call("devdna_repo_summary", {"repo_path": str(SAMPLE)})
    assert summary["component_total"] > 0
    assert summary["routes"] > 0
    assert summary["component_counts"]["route"] == summary["routes"]
    assert summary["edge_count"] > 0
    assert summary["languages"].get("Python", 0) > 0


def test_blast_radius_predicts_post_orders():
    result = _tool_call(
        "devdna_blast_radius",
        {"repo_path": str(SAMPLE), "target_file": "shoplite/services/pricing.py"},
    )
    assert result["prediction"] == "PREDICTED"
    assert result["verified"] is False
    assert result["components_analyzed"] >= 1
    assert result["affected_component_count"] > 0
    assert any(route["name"] == "POST /api/orders" for route in result["impacted_routes"])
    assert result["tests_predicted_to_fail"]


def test_traversal_is_rejected_as_an_error():
    response = handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "devdna_repo_summary",
            "arguments": {"repo_path": "../secret.py"},
        },
    })
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    assert "traversal" in result["content"][0]["text"].lower()


def test_target_file_traversal_is_rejected_as_an_error():
    response = handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "devdna_blast_radius",
            "arguments": {"repo_path": str(SAMPLE), "target_file": "../secret.py"},
        },
    })
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    assert "traversal" in result["content"][0]["text"].lower()


def test_stdio_transport_roundtrip():
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "app.mcp_server"],
        cwd=str(BACKEND),
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [response["id"] for response in responses] == [1, 2]
    assert responses[0]["result"]["serverInfo"]["name"] == "devdna-mcp"
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {
        "devdna_blast_radius",
        "devdna_repo_summary",
    }
