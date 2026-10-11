"""逐事件比较 Python 规范参考实现与 C++ 正式深入口。"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import random
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterable, Sequence


@dataclass(frozen=True)
class SeriesCase:
    name: str
    markers: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]


def load_reference(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("segment_reference", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法载入参考实现：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pivot_series(prices: Sequence[float], name: str) -> SeriesCase:
    if len(prices) < 2:
        raise ValueError("拐点序列至少需要两个价格")
    directions = [
        1 if right > left else -1
        for left, right in zip(prices, prices[1:])
    ]
    if any(left == right for left, right in zip(prices, prices[1:])):
        raise ValueError("拐点价格必须严格变化")
    if any(a == b for a, b in zip(directions, directions[1:])):
        raise ValueError("拐点序列必须方向交替")

    first_marker = -1.0 if directions[0] > 0 else 1.0
    markers = [first_marker]
    for direction in directions:
        markers.append(1.0 if direction > 0 else -1.0)
    values = tuple(float(price) for price in prices)
    return SeriesCase(name, tuple(markers), values, values)


def expanded_pivot_series(
    prices: Sequence[float],
    rng: random.Random,
    name: str,
) -> SeriesCase:
    markers: list[float] = []
    highs: list[float] = []
    lows: list[float] = []

    first_up = prices[1] > prices[0]
    markers.append(-1.0 if first_up else 1.0)
    highs.append(float(prices[0]))
    lows.append(float(prices[0]))

    for start, end in zip(prices, prices[1:]):
        gap = rng.randint(1, 4)
        for step in range(1, gap):
            ratio = step / gap
            center = start + (end - start) * ratio
            max_width = max(abs(end - start) * 0.12, 0.01)
            width = rng.uniform(0.0, max_width)
            highs.append(float(center + width))
            lows.append(float(center - width))
            markers.append(0.0)
        highs.append(float(end))
        lows.append(float(end))
        markers.append(1.0 if end > start else -1.0)

    return SeriesCase(name, tuple(markers), tuple(highs), tuple(lows))


def mirrored_series(case: SeriesCase, name: str) -> SeriesCase:
    return SeriesCase(
        name,
        tuple(-marker for marker in case.markers),
        tuple(-low for low in case.lows),
        tuple(-high for high in case.highs),
    )


def prefix_cases(case: SeriesCase) -> list[SeriesCase]:
    return [
        SeriesCase(
            f"{case.name}_prefix_{length}",
            case.markers[:length],
            case.highs[:length],
            case.lows[:length],
        )
        for length in range(1, len(case.markers))
    ]


def literal_pivot_cases(reference_path: Path) -> list[SeriesCase]:
    tree = ast.parse(reference_path.read_text(encoding="utf-8"))
    unique: set[tuple[float, ...]] = set()
    answer: list[SeriesCase] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_pens_from_pivot_prices":
            continue
        try:
            literal = ast.literal_eval(node.args[0])
            prices = tuple(float(value) for value in literal)
            case = pivot_series(prices, f"literal_pivots_line_{node.lineno}")
        except (TypeError, ValueError, SyntaxError):
            continue
        if prices not in unique:
            unique.add(prices)
            answer.append(case)
    return answer


def random_prices(rng: random.Random, pivot_count: int) -> tuple[float, ...]:
    prices = [rng.uniform(-25.0, 25.0)]
    direction = rng.choice((-1.0, 1.0))
    for _ in range(pivot_count - 1):
        prices.append(prices[-1] + direction * rng.uniform(0.25, 14.0))
        direction = -direction
    return tuple(prices)


def invalid_cases() -> list[SeriesCase]:
    return [
        SeriesCase("empty", (), (), ()),
        SeriesCase("invalid_marker", (-1.0, 0.25, 1.0), (1.0, 2.0, 4.0), (0.0, 1.0, 3.0)),
        SeriesCase(
            "endpoint_extensions",
            (-1.0, 1.0, 1.0, -1.0, -1.0, 1.0),
            (2.0, 5.0, 6.0, 4.0, 3.0, 7.0),
            (1.0, 4.0, 5.0, 3.0, 2.0, 6.0),
        ),
        SeriesCase("redundant_endpoint_update", (-1.0, 1.0, 1.0), (2.0, 6.0, 5.0), (1.0, 5.0, 4.0)),
        SeriesCase("equal_extreme_update", (-1.0, 1.0, 1.0, -1.0), (2.0, 6.0, 6.0, 4.0), (1.0, 5.0, 5.0, 3.0)),
        SeriesCase("high_below_low", (-1.0, 0.0, 1.0), (1.0, 1.0, 4.0), (0.0, 2.0, 3.0)),
        SeriesCase("invalid_pen_direction", (-1.0, 1.0), (6.0, 4.0), (5.0, 3.0)),
    ]


def build_cases(
    reference: ModuleType,
    reference_path: Path,
    random_case_count: int,
    seed: int,
) -> list[SeriesCase]:
    rng = random.Random(seed)
    curated = literal_pivot_cases(reference_path)

    for helper_name in (
        "_case_1_intrapen_tdx_pens",
        "_case_1_standard_B_threshold_tdx_pens",
    ):
        pens = getattr(reference, helper_name)()
        markers, highs, lows = reference._tdx_series_from_pens_for_selfcheck(pens)
        curated.append(SeriesCase(helper_name, markers, highs, lows))

    cases: list[SeriesCase] = []
    for case in curated:
        cases.append(case)
        cases.extend(prefix_cases(case))
        mirror = mirrored_series(case, f"{case.name}_mirror")
        cases.append(mirror)
        cases.extend(prefix_cases(mirror))

    for index in range(random_case_count):
        prices = random_prices(rng, rng.randint(4, 34))
        pivot_case = pivot_series(prices, f"random_pivots_{index}")
        expanded_case = expanded_pivot_series(
            prices,
            rng,
            f"random_expanded_{index}",
        )
        for case in (pivot_case, expanded_case):
            cases.append(case)
            cases.append(mirrored_series(case, f"{case.name}_mirror"))
            if len(case.markers) > 3:
                for length in sorted({
                    max(1, len(case.markers) // 3),
                    max(1, (len(case.markers) * 2) // 3),
                    len(case.markers) - 1,
                }):
                    cases.append(
                        SeriesCase(
                            f"{case.name}_selected_prefix_{length}",
                            case.markers[:length],
                            case.highs[:length],
                            case.lows[:length],
                        )
                    )
    cases.extend(invalid_cases())
    return cases


def serialize_cases(cases: Iterable[SeriesCase]) -> str:
    materialized = list(cases)
    lines = [str(len(materialized))]
    for case in materialized:
        length = len(case.markers)
        if length != len(case.highs) or length != len(case.lows):
            raise AssertionError(f"{case.name}: 三序列长度不一致")
        lines.append(str(length))
        lines.append(" ".join(repr(value) for value in case.markers))
        lines.append(" ".join(repr(value) for value in case.highs))
        lines.append(" ".join(repr(value) for value in case.lows))
    return "\n".join(lines) + "\n"


def calculate_reference(reference: ModuleType, case: SeriesCase):
    # 通达信 ABI 实际传入 float。Python 参考实现必须先看到同一组
    # IEEE-754 binary32 数值，避免拿 Python double 与 C++ float 假对拍。
    def as_float32(value: float) -> float:
        return struct.unpack("<f", struct.pack("<f", value))[0]

    return reference.calculate_tdx_segment_events(
        tuple(as_float32(value) for value in case.markers),
        tuple(as_float32(value) for value in case.highs),
        tuple(as_float32(value) for value in case.lows),
    )


def expected_result(result) -> tuple[int, int, int, tuple[int, ...]]:
    if result.ok:
        return 0, 0, -1, tuple(int(value) for value in result.event_mask)
    first_error = result.input_errors[0]
    return (
        1,
        int(first_error.code),
        int(first_error.bar_index),
        tuple(int(value) for value in result.event_mask),
    )


def parse_actual(line: str) -> tuple[int, int, int, tuple[int, ...]]:
    values = [int(value) for value in line.split()]
    if len(values) < 4:
        raise AssertionError(f"C++ 输出字段不足：{line!r}")
    status, error, error_bar, length = values[:4]
    mask = tuple(values[4:])
    if len(mask) != length:
        raise AssertionError(f"C++ 输出长度声明 {length}，实际 {len(mask)}")
    return status, error, error_bar, mask


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--baseline-runner", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--random-cases", type=int, default=150)
    parser.add_argument("--seed", type=int, default=20260828)
    args = parser.parse_args()

    reference = load_reference(args.reference.resolve())
    reference.run_grouped_self_checks()
    cases = build_cases(
        reference,
        args.reference.resolve(),
        args.random_cases,
        args.seed,
    )
    serialized = serialize_cases(cases)
    completed = subprocess.run(
        [str(args.runner.resolve())],
        input=serialized,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        print(completed.stderr, file=sys.stderr)
        return completed.returncode
    actual_lines = completed.stdout.splitlines()
    if len(actual_lines) != len(cases):
        print(
            f"C++ 返回 {len(actual_lines)} 行，预期 {len(cases)} 行",
            file=sys.stderr,
        )
        return 10

    if args.baseline_runner is not None:
        baseline = subprocess.run(
            [str(args.baseline_runner.resolve())],
            input=serialized,
            text=True,
            capture_output=True,
            check=False,
        )
        if baseline.returncode != 0:
            print(baseline.stderr, file=sys.stderr)
            return baseline.returncode
        baseline_lines = baseline.stdout.splitlines()
        if len(baseline_lines) != len(cases):
            print(
                "基线 C++ 返回 "
                f"{len(baseline_lines)} 行，预期 {len(cases)} 行",
                file=sys.stderr,
            )
            return 14
        for index, (case, before, after) in enumerate(
            zip(cases, baseline_lines, actual_lines)
        ):
            if before == after:
                continue
            print(
                f"新旧二进制输出漂移：case[{index}] {case.name}",
                file=sys.stderr,
            )
            print(f"markers={case.markers!r}", file=sys.stderr)
            print(f"highs={case.highs!r}", file=sys.stderr)
            print(f"lows={case.lows!r}", file=sys.stderr)
            print(f"baseline={before!r}", file=sys.stderr)
            print(f"current ={after!r}", file=sys.stderr)
            return 15

    observed_event_bits = 0
    observed_end_cases = set()
    for index, (case, line) in enumerate(zip(cases, actual_lines)):
        reference_result = calculate_reference(reference, case)
        expected = expected_result(reference_result)
        for value in expected[3]:
            observed_event_bits |= value
        if reference_result.chain is not None:
            for segment in reference_result.chain.confirmed_segments:
                if (
                    segment.endpoint is None
                    or not reference.has_strict_segment_price_direction(
                        segment.direction,
                        segment.start,
                        segment.endpoint,
                    )
                ):
                    print(
                        "线段净方向性质失败："
                        f"case[{index}] {case.name}, segment={segment!r}",
                        file=sys.stderr,
                    )
                    return 16
                if segment.case_name is not None:
                    observed_end_cases.add(segment.case_name)
        actual = parse_actual(line)
        if actual == expected:
            continue
        print(f"对拍失败：case[{index}] {case.name}", file=sys.stderr)
        print(f"markers={case.markers!r}", file=sys.stderr)
        print(f"highs={case.highs!r}", file=sys.stderr)
        print(f"lows={case.lows!r}", file=sys.stderr)
        print(f"Python={expected!r}", file=sys.stderr)
        print(f"C++   ={actual!r}", file=sys.stderr)
        return 11
    required_bits = 1 | 2 | 4 | 8 | 16 | 32 | 64
    if observed_event_bits & required_bits != required_bits:
        print(
            f"对拍样例覆盖不足：observed_bits={observed_event_bits}",
            file=sys.stderr,
        )
        return 12

    required_end_cases = {
        reference.EndCase.CASE_1_NO_GAP,
        reference.EndCase.CASE_2_WITH_GAP,
        reference.EndCase.TREND_A,
    }
    if not required_end_cases.issubset(observed_end_cases):
        print(
            "对拍样例没有覆盖全部终局分支："
            f"observed={sorted(item.value for item in observed_end_cases)}",
            file=sys.stderr,
        )
        return 13

    baseline_note = (
        "，并与基线二进制逐事件一致"
        if args.baseline_runner is not None
        else ""
    )
    print(
        f"Python/C++ 逐事件对拍通过：{len(cases)} 个快照，"
        f"覆盖三类终局与正式七位协议{baseline_note}，"
        f"seed={args.seed}。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
