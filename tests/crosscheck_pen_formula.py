"""Frozen formula interpreter vs real DLL callbacks, including staged interleaving."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pen_reference", ROOT / "reference/pen_formula_reference.py")
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)
LEGACY = ROOT / "tests/fixtures/legacy_pen_formula.txt"


def call(runner, dll, calls):
    lines = [str(len(calls))]
    for selector, rows in calls:
        lines.append(f"{selector} {len(rows[0])}")
        lines.extend(" ".join(repr(float(v)) for v in row) for row in rows)
    result = subprocess.run([str(runner), str(dll)], input="\n".join(lines)+"\n", text=True, capture_output=True, check=True, timeout=180)
    rows = result.stdout.splitlines()
    if len(rows) != len(calls):
        raise AssertionError("Incomplete DLL output")
    output = []
    for row, (_, arrays) in zip(rows, calls):
        values = row.split()
        if int(values[0]) != len(arrays[0]) or len(values)-1 != len(arrays[0]):
            raise AssertionError("Wrong output length")
        output.append([float(v) for v in values[1:]])
    return output


def cases(count, seed):
    rng = random.Random(seed)
    f32 = reference.f32
    output = []
    for index in range(count):
        size = rng.randint(20, 150)
        middle = 100.0
        high, low, close, days = [], [], [], []
        for i in range(size):
            middle += rng.choice((-5, -2, -1, 0, 1, 2, 5))
            spread = rng.choice((0, .25, 1, 3, 6))
            low.append(f32(middle-spread)); high.append(f32(middle+spread))
            close.append(f32(rng.uniform(low[-1], high[-1])))
            days.append(float(1 + (i // rng.choice((1, 4, 10))) % 31))
        if index % 8 == 0:  # flat/equal extrema, simultaneous fractals and MA ties
            high = [101.] * size; low = [99.] * size; close = [100.] * size
        output.append((f"random-{index}", (high, low, close, days)))
        output.append((f"mirror-{index}", ([f32(200-v) for v in low], [f32(200-v) for v in high], [f32(200-v) for v in close], days)))
        stop = rng.randint(1, size)
        output.append((f"prefix-{index}-{stop}", tuple(x[:stop] for x in (high, low, close, days))))
        if index % 4 == 0:
            output.append((f"same-day-{index}", (high, low, close, [15.] * size)))
            hh, ll = high.copy(), low.copy()
            hh[-1] = f32(hh[-1]+10); ll[-1] = f32(ll[-1]-10)
            output.append((f"last-bar-{index}", (hh, ll, close, days)))
    for size in range(1, 21):
        output.append((f"warmup-{size}", ([2.] * size, [1.] * size, [1.5] * size, [31.] * (size-1)+[1.])))
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runner", type=Path)
    p.add_argument("--dll", type=Path)
    p.add_argument("--build-root", type=Path)
    p.add_argument("--baseline-build", type=Path)
    p.add_argument("--random-cases", type=int, default=150)
    p.add_argument("--seed", type=int, default=20260828)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    targets = [("specified", args.runner, args.dll)] if not args.build_root else [
        (arch, args.build_root / arch / "Release/tdx_dll_runner.exe", args.build_root / arch / "Release/TDXSegment.dll") for arch in ("x86", "x64")]
    report = {"passed": False, "seed": args.seed, "random_groups": args.random_cases,
              "legacy_formula_sha256": hashlib.sha256(LEGACY.read_bytes()).hexdigest(), "architectures": {},
              "oracle": "frozen formula interpreter; host boundary conventions explicitly pinned",
              "tdx_host_comparison": "not_run"}
    started = time.perf_counter()
    samples = cases(args.random_cases, args.seed)
    report["snapshots"] = len(samples)
    nonempty = 0
    for offset in range(0, len(samples), 128):
        batch = samples[offset:offset+128]
        expected_context, expected_flags, markers = [], [], []
        for name, (high, low, close, day) in batch:
            env = reference.evaluate(high, low, close, day, LEGACY)
            context = []
            for i, (a,b) in enumerate(zip(env["CLG"], env["CLD"])):
                trend = 8 if not math.isfinite(a) or not math.isfinite(b) else (1 if a>b else (2 if a<b else 0))
                context.append(float(16 + trend + (4 if i>0 and day[i]!=day[i-1] else 0)))
            expected_context.append(context)
            expected_flags.append([a+2*b for a,b in zip(env["CLG3SAFE"], env["CLD3SAFE"])])
            markers.append(env["CLPENEVENT"])
            nonempty += any(v != 0 for v in env["CLPENEVENT"])
        for arch, runner, dll in targets:
            entry = report["architectures"].setdefault(arch, {"mismatches": 0, "callbacks": 0, "segment_pipeline_callbacks": 0})
            prepared = call(runner, dll, [(10,(rows[2], rows[3], rows[2])) for _,rows in batch])
            if prepared != expected_context:
                raise AssertionError(f"{arch} context differs in batch {offset}")
            # Reverse the next stage: any global 'last invocation' cache would fail.
            actual = list(reversed(call(runner, dll, [(11,(batch[i][1][0],batch[i][1][1],prepared[i])) for i in reversed(range(len(batch)))])))
            if actual != expected_flags:
                i = next(i for i,(a,b) in enumerate(zip(actual,expected_flags)) if a!=b)
                bar = next(j for j,(a,b) in enumerate(zip(actual[i],expected_flags[i])) if a!=b)
                raise AssertionError(f"{arch} {batch[i][0]} flags differ at {bar}: {actual[i][bar]} != {expected_flags[i][bar]}")
            entry["callbacks"] += len(batch)*2
            actual_markers = [[1. if int(v) & 1 else (-1. if int(v) & 2 else 0.) for v in row] for row in actual]
            if actual_markers != markers:
                raise AssertionError(f"{arch} top-priority PENEVENT conversion mismatch")
            if args.baseline_build:
                calls = [(selector,(marker,rows[0],rows[1])) for (_,rows),marker in zip(batch,markers) for selector in (1,2,99)]
                before = call(args.baseline_build/arch/"Release/tdx_dll_runner.exe", args.baseline_build/arch/"Release/TDXSegment.dll", calls)
                candidate_calls = [(selector,(marker,rows[0],rows[1])) for (_,rows),marker in zip(batch,actual_markers) for selector in (1,2,99)]
                after = call(runner, dll, candidate_calls)
                if before != after:
                    raise AssertionError(f"{arch} pen -> old/new segment pipeline mismatch")
                entry["segment_pipeline_callbacks"] += len(calls)
        if offset % 1024 == 0:
            print(f"Pen formula: {min(offset+len(batch),len(samples))}/{len(samples)} snapshots", flush=True)
    if nonempty < args.random_cases // 4:
        raise AssertionError("Corpus failed to produce enough nonempty pen streams")
    report["nonempty_snapshots"] = nonempty
    for arch, runner, dll in targets:
        invalid_calls = [(10,([2,3],[0,1],[2,3])), (10,([2,3],[1,1],[2,1])), (11,([1,1],[2,2],[16,16])),
                         (11,([2,2],[1,1],[16,64])), (11,([2,2],[1,1],[19,16])), (11,([2,2],[1,1],[16.5,16])),
                         (11,([2,2],[1,1],[0,0]))]
        if call(runner,dll,invalid_calls) != [[0.,64.]] * len(invalid_calls):
            raise AssertionError(f"{arch} invalid input did not return atomic error")
        report["architectures"][arch]["invalid_callbacks"] = len(invalid_calls)
    report["passed"] = True
    report["elapsed_seconds"] = time.perf_counter()-started
    if args.output:
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
