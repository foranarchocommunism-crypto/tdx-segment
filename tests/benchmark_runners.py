"""对同一长周期语料严格比较候选与基线 C++ runner 的输出和耗时。"""

from __future__ import annotations

import argparse
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import crosscheck_reference as crosscheck


def long_bar_cases(count: int, seed: int) -> list[crosscheck.SeriesCase]:
    """生成笔间包含较多 K 线、同时包含价格镜像的固定随机语料。"""

    rng = random.Random(seed)
    cases: list[crosscheck.SeriesCase] = []
    for case_index in range(count):
        prices = crosscheck.random_prices(rng, rng.randint(48, 96))
        markers = [-1.0 if prices[1] > prices[0] else 1.0]
        highs = [float(prices[0])]
        lows = [float(prices[0])]
        for start, end in zip(prices, prices[1:]):
            gap = rng.randint(24, 56)
            for step in range(1, gap):
                ratio = step / gap
                center = start + (end - start) * ratio
                width = rng.uniform(
                    0.0,
                    max(abs(end - start) * 0.08, 0.01),
                )
                highs.append(float(center + width))
                lows.append(float(center - width))
                markers.append(0.0)
            highs.append(float(end))
            lows.append(float(end))
            markers.append(1.0 if end > start else -1.0)
        case = crosscheck.SeriesCase(
            f"long_bars_{case_index}",
            tuple(markers),
            tuple(highs),
            tuple(lows),
        )
        cases.append(case)
        cases.append(
            crosscheck.mirrored_series(case, f"{case.name}_mirror")
        )
    return cases


def run_runner(path: Path, serialized: str) -> tuple[str, float]:
    started = time.perf_counter()
    completed = subprocess.run(
        [str(path)],
        input=serialized,
        text=True,
        capture_output=True,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
        raise SystemExit(completed.returncode)
    return completed.stdout, elapsed


def confirmed_segment_count(output_line: str) -> int:
    _, _, _, event_mask = crosscheck.parse_actual(output_line)
    endpoint_bits = 0b11
    return sum(1 for value in event_mask if value & endpoint_bits)


def select_cases(
    baseline: Path,
    requested_count: int,
    seed: int,
    minimum_segments: int,
) -> list[crosscheck.SeriesCase]:
    multiplier = 8 if minimum_segments > 0 else 1
    cases = long_bar_cases(requested_count * multiplier, seed)
    if minimum_segments <= 0:
        return cases

    serialized = crosscheck.serialize_cases(cases)
    baseline_output, _ = run_runner(baseline, serialized)
    baseline_lines = baseline_output.splitlines()
    if len(baseline_lines) != len(cases):
        print(
            f"基线返回 {len(baseline_lines)} 行，预期 {len(cases)} 行。",
            file=sys.stderr,
        )
        raise SystemExit(24)
    selected = [
        case
        for case, line in zip(cases, baseline_lines)
        if confirmed_segment_count(line) >= minimum_segments
    ][: requested_count * 2]
    if len(selected) < requested_count * 2:
        print(
            f"只找到 {len(selected)} 个合格快照，"
            f"预期 {requested_count * 2} 个。",
            file=sys.stderr,
        )
        raise SystemExit(23)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--repetitions", type=int, default=9)
    parser.add_argument("--minimum-segments", type=int, default=0)
    parser.add_argument("--max-ratio", type=float, default=1.0)
    args = parser.parse_args()

    if args.cases <= 0 or args.repetitions <= 0:
        parser.error("--cases 和 --repetitions 必须为正整数")
    if args.minimum_segments < 0 or args.max_ratio <= 0.0:
        parser.error("--minimum-segments 不得为负，--max-ratio 必须为正数")

    candidate = args.candidate.resolve()
    baseline = args.baseline.resolve()
    cases = select_cases(
        baseline,
        args.cases,
        args.seed,
        args.minimum_segments,
    )
    serialized = crosscheck.serialize_cases(cases)

    candidate_output, _ = run_runner(candidate, serialized)
    baseline_output, _ = run_runner(baseline, serialized)
    if len(candidate_output.splitlines()) != len(cases):
        print("候选 runner 返回行数与输入快照数不一致。", file=sys.stderr)
        return 25
    if candidate_output != baseline_output:
        print("候选与基线 runner 输出不一致。", file=sys.stderr)
        return 21

    candidate_times: list[float] = []
    baseline_times: list[float] = []
    for repetition in range(args.repetitions):
        order = ("candidate", "baseline")
        if repetition % 2:
            order = tuple(reversed(order))
        for label in order:
            path = candidate if label == "candidate" else baseline
            output, elapsed = run_runner(path, serialized)
            if output != candidate_output:
                print(f"{label} 在测量期间输出发生变化。", file=sys.stderr)
                return 22
            target = (
                candidate_times
                if label == "candidate"
                else baseline_times
            )
            target.append(elapsed)

    candidate_median = statistics.median(candidate_times)
    baseline_median = statistics.median(baseline_times)
    ratio = candidate_median / baseline_median
    print(
        f"cases={len(cases)} "
        f"input_bytes={len(serialized.encode('utf-8'))} "
        f"repetitions={args.repetitions} "
        f"minimum_segments={args.minimum_segments}"
    )
    print(
        "candidate_seconds="
        f"{candidate_median:.6f} "
        f"range=[{min(candidate_times):.6f},{max(candidate_times):.6f}]"
    )
    print(
        "baseline_seconds="
        f"{baseline_median:.6f} "
        f"range=[{min(baseline_times):.6f},{max(baseline_times):.6f}]"
    )
    print(f"candidate_over_baseline={ratio:.6f}")
    if ratio > args.max_ratio:
        print(
            f"性能门禁失败：{ratio:.6f} > {args.max_ratio:.6f}",
            file=sys.stderr,
        )
        return 20
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
