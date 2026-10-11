"""逐字段比较 Python 规范与 C++ 线段链审计结果。"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import crosscheck_reference as crosscheck


def bar(point) -> int:
    return -1 if point is None else int(point.order)


def direction_value(reference, direction) -> int:
    return 1 if direction == reference.Direction.UP else -1


def status_value(reference, status) -> int:
    return {
        reference.SegmentStatus.CONFIRMED: 0,
        reference.SegmentStatus.PENDING: 1,
        reference.SegmentStatus.INVALID_INPUT: 2,
    }[status]


def end_case_value(reference, end_case) -> int:
    if end_case is None:
        return -1
    return {
        reference.EndCase.CASE_1_NO_GAP: 0,
        reference.EndCase.CASE_2_WITH_GAP: 1,
        reference.EndCase.TREND_A: 2,
        reference.EndCase.LONG_STALL_RECOVERY: 3,
    }[end_case]


def segment_tokens(reference, segment) -> list[str]:
    intrinsic = segment.proof.get(
        "intrinsic_segment_confirmed_at",
        segment.confirmed_at,
    )
    return [
        str(direction_value(reference, segment.direction)),
        str(status_value(reference, segment.status)),
        str(bar(segment.start)),
        str(bar(segment.endpoint)),
        str(bar(intrinsic)),
        str(bar(segment.confirmed_at)),
        str(end_case_value(reference, segment.case_name)),
        str(bar(segment.next_segment_t_not_before)),
    ]


def expected_line(reference, calculation) -> str:
    if not calculation.ok:
        error = calculation.input_errors[0]
        return f"E {int(error.code)} {int(error.bar_index)}"
    if calculation.initial_start is None:
        return "N"
    assert calculation.chain is not None
    chain = calculation.chain
    tokens = [
        "C",
        str(calculation.initial_start.start_pos),
        str(direction_value(reference, calculation.initial_start.direction)),
        str(len(chain.confirmed_segments)),
    ]
    for segment in chain.confirmed_segments:
        tokens.extend(segment_tokens(reference, segment))
    tokens.append("1" if chain.unresolved_segment is not None else "0")
    if chain.unresolved_segment is not None:
        tokens.extend(segment_tokens(reference, chain.unresolved_segment))
    tokens.extend(
        [
            str(bar(chain.next_start)),
            str(direction_value(reference, chain.next_direction)),
            str(bar(chain.next_segment_t_not_before)),
        ]
    )
    return " ".join(tokens)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--random-cases", type=int, default=150)
    parser.add_argument("--seed", type=int, default=20260828)
    args = parser.parse_args()

    reference = crosscheck.load_reference(args.reference.resolve())
    cases = crosscheck.build_cases(
        reference,
        args.reference.resolve(),
        args.random_cases,
        args.seed,
    )
    completed = subprocess.run(
        [str(args.runner.resolve())],
        input=crosscheck.serialize_cases(cases),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        print(completed.stderr)
        return completed.returncode
    actual_lines = completed.stdout.splitlines()
    if len(actual_lines) != len(cases):
        print(f"C++ 审计返回 {len(actual_lines)} 行，预期 {len(cases)} 行")
        return 10

    for index, (case, actual) in enumerate(zip(cases, actual_lines)):
        expected = expected_line(
            reference,
            crosscheck.calculate_reference(reference, case),
        )
        if actual == expected:
            continue
        print(f"审计字段对拍失败：case[{index}] {case.name}")
        print(f"Python={expected}")
        print(f"C++   ={actual}")
        return 11
    print(
        f"Python/C++ 线段链逐字段对拍通过：{len(cases)} 个快照，"
        f"seed={args.seed}。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
