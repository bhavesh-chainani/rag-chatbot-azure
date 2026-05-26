import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = ROOT / "data" / "pbsg_golden_set_by_id"
DEFAULT_OUTPUT = ROOT / "evals" / "results" / "pbsg_golden_set_eval.json"
MULTI_TOPIC_CASES = [
    {
        "query": (
            "Applicant says she has a criminal charge, wants to divorce her husband, "
            "is a Singapore Citizen, and will be charged on 28 May 2026."
        ),
        "expected_primary": "GEN3-T02",
        "expected_queued": ["GEN3-T03"],
        "expected_monitors": ["GEN3-T06"],
    },
    {
        "query": "Applicant is elderly, has a court date next week, and wants help with a debt issue.",
        "expected_primary": "GEN3-T04",
        "expected_queued": [],
        "expected_monitors": ["GEN3-T06", "GEN3-T13"],
    },
]


def load_golden_dataset(path: Path) -> list[dict[str, Any]]:
    """Load one combined list from a directory of per-id JSON files or a legacy array JSON file."""
    if path.is_dir():
        paths = sorted(path.glob("*.json"), key=lambda p: p.stem)
        if not paths:
            raise FileNotFoundError(f"No *.json entries under {path}")
        return [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    return json.loads(path.read_text(encoding="utf-8"))


SELECTED_ENTRY_RE = re.compile(
    r"Selected Entry:\s*([A-Z]{3}-\d{2}|GEN3-[A-Z0-9-]+|Unclear)",
    re.IGNORECASE,
)
PBSG_STATE_MARKER_RE = re.compile(r"<!--\s*pbsg-state:\s*(?P<body>.*?)\s*-->", re.IGNORECASE | re.DOTALL)
ROUTE_RE = re.compile(r"\bRoute\s+([A-Z])\b", re.IGNORECASE)


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_question(question: str) -> str:
    question = re.sub(r"^\s*Q\d+:\s*", "", question, flags=re.IGNORECASE).strip()
    return normalize_text(question)


def selected_entry_from_response(response: dict[str, Any], content: str) -> str | None:
    context = response.get("context") or {}
    triage_state = context.get("pbsg_triage_state") or {}
    for key in ("pending_entry_id", "active_workflow", "workflow_id"):
        value = triage_state.get(key)
        if isinstance(value, str) and value.startswith("GEN3-"):
            return value

    marker_matches = list(PBSG_STATE_MARKER_RE.finditer(content))
    if marker_matches:
        marker_body = marker_matches[-1].group("body")
        marker_selected = re.search(r"\bselected_entry=([^;\s]+)", marker_body, flags=re.IGNORECASE)
        if marker_selected:
            return marker_selected.group(1).upper()

    selected_match = SELECTED_ENTRY_RE.search(content)
    return selected_match.group(1).upper() if selected_match else None


def post_chat(
    target_url: str,
    messages: list[dict[str, str]],
    overrides: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    url = f"{target_url.rstrip('/')}/chat"
    payload = {"messages": messages, "context": {"overrides": overrides}}
    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
        return json.loads(body)


def evaluate_phase1_case(
    target_url: str,
    query: str,
    expected_entry_id: str,
    expected_questions: list[str],
    overrides: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.time()
    result: dict[str, Any] = {
        "query": query,
        "expected_entry_id": expected_entry_id,
        "selected_entry_ok": False,
        "part_b_completeness": 0.0,
        "missing_questions": [],
        "error": None,
        "latency_ms": None,
    }
    try:
        response = post_chat(
            target_url=target_url,
            messages=[{"role": "user", "content": query}],
            overrides=overrides,
            timeout_seconds=timeout_seconds,
        )
        content = ((response.get("message") or {}).get("content") or "").strip()
        result["latency_ms"] = int((time.time() - started) * 1000)

        selected = selected_entry_from_response(response, content)
        result["selected_entry"] = selected
        result["selected_entry_ok"] = selected == expected_entry_id

        normalized_answer = normalize_text(content)
        normalized_expected_questions = [normalize_question(q) for q in expected_questions]
        missing = [q for q, nq in zip(expected_questions, normalized_expected_questions) if nq not in normalized_answer]
        result["missing_questions"] = missing
        total_questions = len(expected_questions)
        result["part_b_completeness"] = (
            1.0 if total_questions == 0 else (total_questions - len(missing)) / total_questions
        )
    except urllib.error.HTTPError as exc:
        result["error"] = f"HTTPError {exc.code}: {exc.reason}"
        result["latency_ms"] = int((time.time() - started) * 1000)
    except Exception as exc:
        result["error"] = str(exc)
        result["latency_ms"] = int((time.time() - started) * 1000)
    return result


def evaluate_phase2_case(
    target_url: str,
    query: str,
    expected_entry_id: str,
    expected_routes: list[str],
    overrides: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.time()
    result: dict[str, Any] = {
        "query": query,
        "expected_entry_id": expected_entry_id,
        "selected_entry_ok": False,
        "route_labels_in_response": [],
        "invalid_route_labels": [],
        "route_validity_applicable": False,
        "route_validity_ok": None,
        "error": None,
        "latency_ms": None,
    }

    expected_labels = sorted(
        {match.group(1).upper() for route in expected_routes for match in ROUTE_RE.finditer(route)}
    )

    phase2_user_message = (
        "Applicant answered the Part B questions. "
        "Assume applicant is within time limits and has not filed yet. "
        "Provide Part C routing recommendation only."
    )
    try:
        response = post_chat(
            target_url=target_url,
            messages=[{"role": "user", "content": query}, {"role": "user", "content": phase2_user_message}],
            overrides=overrides,
            timeout_seconds=timeout_seconds,
        )
        content = ((response.get("message") or {}).get("content") or "").strip()
        result["latency_ms"] = int((time.time() - started) * 1000)

        selected = selected_entry_from_response(response, content)
        result["selected_entry"] = selected
        result["selected_entry_ok"] = selected == expected_entry_id

        route_labels = sorted({m.group(1).upper() for m in ROUTE_RE.finditer(content)})
        result["route_labels_in_response"] = route_labels
        if route_labels:
            invalid = [label for label in route_labels if label not in expected_labels]
            result["invalid_route_labels"] = invalid
            result["route_validity_applicable"] = True
            result["route_validity_ok"] = len(invalid) == 0
    except urllib.error.HTTPError as exc:
        result["error"] = f"HTTPError {exc.code}: {exc.reason}"
        result["latency_ms"] = int((time.time() - started) * 1000)
    except Exception as exc:
        result["error"] = str(exc)
        result["latency_ms"] = int((time.time() - started) * 1000)

    return result


def evaluate_multi_topic_case(
    target_url: str,
    case: dict[str, Any],
    overrides: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.time()
    result: dict[str, Any] = {
        "query": case["query"],
        "expected_primary": case["expected_primary"],
        "expected_queued": case["expected_queued"],
        "expected_monitors": case["expected_monitors"],
        "primary_ok": False,
        "queued_recall": 0.0,
        "monitor_recall": 0.0,
        "unnecessary_general_fallback": False,
        "error": None,
        "latency_ms": None,
    }
    try:
        response = post_chat(
            target_url=target_url,
            messages=[{"role": "user", "content": case["query"]}],
            overrides=overrides,
            timeout_seconds=timeout_seconds,
        )
        content = ((response.get("message") or {}).get("content") or "").strip()
        context = response.get("context") or {}
        triage_state = context.get("pbsg_triage_state") or {}
        result["latency_ms"] = int((time.time() - started) * 1000)

        selected = selected_entry_from_response(response, content) or triage_state.get("active_workflow")
        queued = triage_state.get("queued_workflows") or []
        monitors = triage_state.get("triggered_overlays") or []
        result["selected_entry"] = selected
        result["queued_workflows"] = queued
        result["monitor_workflows"] = monitors
        result["primary_ok"] = selected == case["expected_primary"]
        result["unnecessary_general_fallback"] = selected == "GEN3-T01" and case["expected_primary"] != "GEN3-T01"

        expected_queued = set(case["expected_queued"])
        expected_monitors = set(case["expected_monitors"])
        result["queued_recall"] = (
            1.0 if not expected_queued else len(expected_queued & set(queued)) / len(expected_queued)
        )
        result["monitor_recall"] = (
            1.0 if not expected_monitors else len(expected_monitors & set(monitors)) / len(expected_monitors)
        )
    except urllib.error.HTTPError as exc:
        result["error"] = f"HTTPError {exc.code}: {exc.reason}"
        result["latency_ms"] = int((time.time() - started) * 1000)
    except Exception as exc:
        result["error"] = str(exc)
        result["latency_ms"] = int((time.time() - started) * 1000)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate PBSG golden set behavior through /chat endpoint.")
    parser.add_argument("--targeturl", type=str, required=True, help="Backend base URL, e.g. http://127.0.0.1:50505")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_DIR,
        help="Directory of per-id JSON files (default: data/pbsg_golden_set_by_id) or a single array JSON file",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path for evaluation report JSON")
    parser.add_argument("--max-entries", type=int, default=0, help="Limit entries (0 means all)")
    parser.add_argument("--per-entry-variations", type=int, default=0, help="Limit variations per entry (0 means all)")
    parser.add_argument("--timeout-seconds", type=int, default=90, help="HTTP timeout per request")
    args = parser.parse_args()

    dataset = load_golden_dataset(args.dataset)
    entries = dataset[: args.max_entries] if args.max_entries and args.max_entries > 0 else dataset

    overrides = {
        "retrieval_mode": "hybrid",
        "semantic_ranker": True,
        "semantic_captions": False,
        "suggest_followup_questions": False,
        "query_rewriting": True,
        "top": 5,
    }

    phase1_cases: list[dict[str, Any]] = []
    phase2_cases: list[dict[str, Any]] = []

    for entry in entries:
        entry_variations = entry.get("variations", [])
        if not entry_variations:
            entry_variations = [entry["user_query"]]
        if args.per_entry_variations and args.per_entry_variations > 0:
            entry_variations = entry_variations[: args.per_entry_variations]

        for variation in entry_variations:
            phase1_cases.append(
                evaluate_phase1_case(
                    target_url=args.targeturl,
                    query=variation,
                    expected_entry_id=entry["id"],
                    expected_questions=entry.get("triage_questions", []),
                    overrides=overrides,
                    timeout_seconds=args.timeout_seconds,
                )
            )
            phase2_cases.append(
                evaluate_phase2_case(
                    target_url=args.targeturl,
                    query=variation,
                    expected_entry_id=entry["id"],
                    expected_routes=entry.get("routing", []),
                    overrides=overrides,
                    timeout_seconds=args.timeout_seconds,
                )
            )

    multi_topic_cases = [
        evaluate_multi_topic_case(
            target_url=args.targeturl,
            case=case,
            overrides=overrides,
            timeout_seconds=args.timeout_seconds,
        )
        for case in MULTI_TOPIC_CASES
    ]

    phase1_total = len(phase1_cases)
    phase1_no_error = [c for c in phase1_cases if not c["error"]]
    phase1_selected_entry_rate = (
        sum(1 for c in phase1_no_error if c["selected_entry_ok"]) / len(phase1_no_error) if phase1_no_error else 0.0
    )
    phase1_avg_partb_completeness = (
        sum(c["part_b_completeness"] for c in phase1_no_error) / len(phase1_no_error) if phase1_no_error else 0.0
    )

    phase2_total = len(phase2_cases)
    phase2_no_error = [c for c in phase2_cases if not c["error"]]
    phase2_selected_entry_rate = (
        sum(1 for c in phase2_no_error if c["selected_entry_ok"]) / len(phase2_no_error) if phase2_no_error else 0.0
    )
    phase2_applicable = [c for c in phase2_no_error if c["route_validity_applicable"]]
    phase2_route_valid_rate = (
        sum(1 for c in phase2_applicable if c["route_validity_ok"]) / len(phase2_applicable)
        if phase2_applicable
        else 0.0
    )
    multi_topic_no_error = [c for c in multi_topic_cases if not c["error"]]
    multi_topic_primary_rate = (
        sum(1 for c in multi_topic_no_error if c["primary_ok"]) / len(multi_topic_no_error)
        if multi_topic_no_error
        else 0.0
    )
    multi_topic_queued_recall = (
        sum(c["queued_recall"] for c in multi_topic_no_error) / len(multi_topic_no_error)
        if multi_topic_no_error
        else 0.0
    )
    multi_topic_monitor_recall = (
        sum(c["monitor_recall"] for c in multi_topic_no_error) / len(multi_topic_no_error)
        if multi_topic_no_error
        else 0.0
    )
    unnecessary_general_fallback_rate = (
        sum(1 for c in multi_topic_no_error if c["unnecessary_general_fallback"]) / len(multi_topic_no_error)
        if multi_topic_no_error
        else 0.0
    )

    report = {
        "metadata": {
            "target_url": args.targeturl,
            "dataset": str(args.dataset),
            "entry_count": len(entries),
            "total_cases": phase1_total,
            "timestamp_unix": int(time.time()),
        },
        "summary": {
            "phase1": {
                "total_cases": phase1_total,
                "success_cases": len(phase1_no_error),
                "error_cases": phase1_total - len(phase1_no_error),
                "selected_entry_accuracy": round(phase1_selected_entry_rate, 4),
                "avg_part_b_completeness": round(phase1_avg_partb_completeness, 4),
            },
            "phase2": {
                "total_cases": phase2_total,
                "success_cases": len(phase2_no_error),
                "error_cases": phase2_total - len(phase2_no_error),
                "selected_entry_accuracy": round(phase2_selected_entry_rate, 4),
                "route_validity_cases": len(phase2_applicable),
                "route_validity_rate": round(phase2_route_valid_rate, 4),
            },
            "multi_topic": {
                "total_cases": len(multi_topic_cases),
                "success_cases": len(multi_topic_no_error),
                "error_cases": len(multi_topic_cases) - len(multi_topic_no_error),
                "primary_topic_accuracy": round(multi_topic_primary_rate, 4),
                "queued_topic_recall": round(multi_topic_queued_recall, 4),
                "monitor_recall": round(multi_topic_monitor_recall, 4),
                "unnecessary_gen3_t01_fallback_rate": round(unnecessary_general_fallback_rate, 4),
            },
        },
        "phase1_cases": phase1_cases,
        "phase2_cases": phase2_cases,
        "multi_topic_cases": multi_topic_cases,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote report to {args.output}")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
