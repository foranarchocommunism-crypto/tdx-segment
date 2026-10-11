"""Frozen native/DLL migration equivalence plus the supported Python oracle."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True
import crosscheck_reference as crosscheck

ROOT = Path(__file__).resolve().parents[1]
POLICIES = {"1": [], "2": ["--recovery"], "OFF": ["--baseline"]}
EXPECTED_TESTS = {
    "vendor_abi_layout_selftest", "core_selftest", "primary_left_a_scope_selftest",
    "pen_decoder_selftest", "plugin_load_selftest", "plugin_atomic_selftest",
    "many_short_segments_performance_regression", "python_cpp_reference_crosscheck",
    "python_cpp_audit_crosscheck", "window_growth_performance_regression",
    "audit_seam_selftest", "pen_range_selftest", "evaluation_continuation_selftest",
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(command: list[str], serialized: str = "", timeout: int = 1800) -> str:
    result = subprocess.run(command, input=serialized, text=True, encoding="utf-8", capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name}: exit {result.returncode}: {result.stderr[-2000:]}")
    return result.stdout


def ctest_result(build: Path) -> list[dict]:
    suite = ET.parse(build / "ctest-results.xml").getroot()
    cases = suite.findall("testcase")
    if {c.get("name") for c in cases} != EXPECTED_TESTS or len(cases) != 13:
        raise RuntimeError("Expected all 13 CTest entries")
    if any(c.find("failure") is not None or c.find("skipped") is not None or c.get("status") != "run" for c in cases):
        raise RuntimeError("CTest has failures, skipped entries or non-run entries")
    return [{"name": c.get("name"), "seconds": float(c.get("time", "0")), "output": c.findtext("system-out", "")} for c in cases]


def dll_input(cases: list) -> str:
    lines = [str(len(cases) * 3)]
    for case in cases:
        length = len(case.markers)
        arrays = [" ".join(repr(v) for v in row) for row in (case.markers, case.highs, case.lows)]
        for selector in (1, 2, 99):
            lines.extend([f"{selector} {length}", *arrays])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-build", required=True, type=Path)
    parser.add_argument("--candidate-build", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--random-cases", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--batch-size", type=int, default=512)
    args = parser.parse_args()
    if args.random_cases < 5000 or args.seed != 20260828 or args.batch_size < 1:
        parser.error("Release validation requires >=5000 cases, seed=20260828, batch-size>=1")
    commit = git("rev-parse", "HEAD")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Validate a clean source commit")
    report = {"schema": "tdx-segment-release-validation-v1", "version": (ROOT / "VERSION").read_text().strip(), "source_commit": commit, "seed": args.seed, "random_groups": args.random_cases, "passed": False, "architectures": {}, "remote_ci": "not_run", "client_manual_validation": "not_run", "historical_full_final_gate": False, "historical_algorithm_99_percent_gate": "not_achieved"}
    started = time.perf_counter()
    try:
        reference_path = ROOT / "reference" / "segment_reference.py"
        reference = crosscheck.load_reference(reference_path)
        cases = crosscheck.build_cases(reference, reference_path, args.random_cases, args.seed)
        report["snapshot_count"] = len(cases)
        print(f"Generated {len(cases)} snapshots from {args.random_cases} random groups", flush=True)
        for arch in ("x86", "x64"):
            base = args.baseline_build.resolve() / arch / "Release"
            candidate = args.candidate_build.resolve() / arch / "Release"
            entry = {"ctest": ctest_result(candidate.parent), "dll_sha256": sha(candidate / "TDXSegment.dll"), "baseline_dll_sha256": sha(base / "TDXSegment.dll"), "channels": {}}
            report["architectures"][arch] = entry
            for channel, executable, extra in (("events", "tdx_segment_vector_runner.exe", []), ("full_audit", "tdx_segment_audit_runner.exe", ["--full-json"])):
                for policy, flags in POLICIES.items():
                    digest = hashlib.sha256()
                    for offset in range(0, len(cases), args.batch_size):
                        batch = cases[offset:offset + args.batch_size]
                        data = crosscheck.serialize_cases(batch)
                        before = invoke([str(base / executable), *flags, *extra], data)
                        after = invoke([str(candidate / executable), *flags, *extra], data)
                        if len(after.splitlines()) != len(batch):
                            raise RuntimeError(f"{arch}/{channel}/{policy}: incomplete output")
                        if before != after:
                            first = next(i for i, pair in enumerate(zip(before.splitlines(), after.splitlines())) if pair[0] != pair[1])
                            raise RuntimeError(f"{arch}/{channel}/{policy}: mismatch at {cases[offset + first].name}")
                        digest.update(after.encode("utf-8"))
                    entry["channels"][f"{channel}/{policy}"] = {"snapshots": len(cases), "mismatches": 0, "sha256": digest.hexdigest()}
                    print(f"{arch}: {channel}/{policy} {len(cases)} snapshots, zero differences", flush=True)
            digest = hashlib.sha256()
            for offset in range(0, len(cases), args.batch_size):
                batch = cases[offset:offset + args.batch_size]
                data = dll_input(batch)
                before = invoke([str(base / "tdx_dll_runner.exe"), str(base / "TDXSegment.dll")], data)
                after = invoke([str(candidate / "tdx_dll_runner.exe"), str(candidate / "TDXSegment.dll")], data)
                if before != after or len(after.splitlines()) != len(batch) * 3:
                    raise RuntimeError(f"{arch}: registered DLL callback mismatch, batch {offset}")
                if "nan" in after.lower():
                    raise RuntimeError(f"{arch}: DLL output not fully written")
                # Native formal/experimental masks must also equal actual callback output.
                lines = after.splitlines()
                for selector, flags in ((1, []), (2, ["--recovery"])):
                    native = invoke([str(candidate / "tdx_segment_vector_runner.exe"), *flags], crosscheck.serialize_cases(batch)).splitlines()
                    for i, line in enumerate(native):
                        values = line.split()
                        expected = [values[3], *values[4:]]
                        actual = lines[i * 3 + selector - 1].split()
                        if expected != actual:
                            raise RuntimeError(f"{arch}: DLL/native disagreement selector {selector}, {batch[i].name}")
                digest.update(after.encode("utf-8"))
            entry["channels"]["actual_dll/1,2,99"] = {"calls": len(cases) * 3, "mismatches": 0, "sha256": digest.hexdigest()}
            print(f"{arch}: actual DLL 1/2/99, {len(cases) * 3} calls, zero differences", flush=True)
            for kind, script, runner in (("events", "crosscheck_reference.py", "tdx_segment_vector_runner.exe"), ("audit", "crosscheck_audit.py", "tdx_segment_audit_runner.exe")):
                print(f"{arch}: checking Python/{kind} ...", flush=True)
                output = invoke([sys.executable, "-B", str(ROOT / "tests" / script), "--runner", str(candidate / runner), "--reference", str(reference_path), "--random-cases", str(args.random_cases), "--seed", str(args.seed)])
                entry[f"python_{kind}"] = {"passed": True, "output": output.strip()}
                print(output.strip(), flush=True)
        if git("rev-parse", "HEAD") != commit or git("status", "--porcelain", "--untracked-files=all"):
            raise RuntimeError("Source changed during validation")
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        report["elapsed_seconds"] = time.perf_counter() - started
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
