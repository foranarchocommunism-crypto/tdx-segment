"""复现通达信逐步扩大 K 线窗口时的单次重算耗时增长。"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import crosscheck_reference as crosscheck


def build_long_series(length: int, seed: int, bars_per_pen: int) -> crosscheck.SeriesCase:
    """生成固定右端点、笔方向严格交替的长 K 线序列。"""

    if length < 3 or bars_per_pen < 2:
        raise ValueError("length 至少为 3，bars_per_pen 至少为 2")
    rng = random.Random(seed)
    pivot_count = math.ceil((length - 1) / bars_per_pen) + 1
    prices = crosscheck.random_prices(rng, pivot_count)

    markers: list[float] = [-1.0 if prices[1] > prices[0] else 1.0]
    highs: list[float] = [prices[0]]
    lows: list[float] = [prices[0]]
    for start, end in zip(prices, prices[1:]):
        for step in range(1, bars_per_pen + 1):
            ratio = step / bars_per_pen
            center = start + (end - start) * ratio
            if step == bars_per_pen:
                width = 0.0
                marker = 1.0 if end > start else -1.0
            else:
                width = abs(end - start) * (0.015 + 0.01 * (step % 3))
                marker = 0.0
            highs.append(center + width)
            lows.append(center - width)
            markers.append(marker)

    return crosscheck.SeriesCase(
        "window_growth_base",
        tuple(markers[-length:]),
        tuple(highs[-length:]),
        tuple(lows[-length:]),
    )


def slice_window(
    base: crosscheck.SeriesCase,
    length: int,
    mode: str,
) -> crosscheck.SeriesCase:
    if mode == "prepend":
        selection = slice(-length, None)
    else:
        selection = slice(0, length)
    return crosscheck.SeriesCase(
        f"{mode}_{length}",
        base.markers[selection],
        base.highs[selection],
        base.lows[selection],
    )


def measure_runner(
    runner: Path,
    case: crosscheck.SeriesCase,
    calls_per_sample: int,
    repetitions: int,
) -> float:
    serialized = crosscheck.serialize_cases([case] * calls_per_sample)
    samples: list[float] = []
    for _ in range(repetitions):
        started = time.perf_counter()
        completed = subprocess.run(
            [str(runner)],
            input=serialized,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        elapsed = time.perf_counter() - started
        if completed.returncode != 0:
            print(completed.stderr, file=sys.stderr)
            raise SystemExit(completed.returncode)
        samples.append(elapsed / calls_per_sample)
    return statistics.median(samples)


def growth_exponent(lengths: list[int], seconds: list[float]) -> float:
    """计算 log(time) 对 log(length) 的最小二乘斜率。"""

    xs = [math.log(value) for value in lengths]
    ys = [math.log(value) for value in seconds]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    return numerator / denominator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--lengths", default="512,1024,2048,4096")
    parser.add_argument("--mode", choices=("prepend", "append"), default="prepend")
    parser.add_argument("--bars-per-pen", type=int, default=12)
    parser.add_argument("--calls-per-sample", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--max-exponent", type=float, default=1.35)
    args = parser.parse_args()

    lengths = [int(value) for value in args.lengths.split(",")]
    if (
        len(lengths) < 3
        or lengths != sorted(set(lengths))
        or lengths[0] < 3
        or args.calls_per_sample <= 0
        or args.repetitions <= 0
    ):
        parser.error("窗口长度必须是至少三个严格递增正整数，采样参数必须为正")

    runner = args.runner.resolve()
    base = build_long_series(lengths[-1], args.seed, args.bars_per_pen)
    elapsed_seconds: list[float] = []
    for length in lengths:
        case = slice_window(base, length, args.mode)
        elapsed = measure_runner(
            runner,
            case,
            args.calls_per_sample,
            args.repetitions,
        )
        elapsed_seconds.append(elapsed)
        print(
            f"mode={args.mode} length={length} "
            f"seconds_per_call={elapsed:.6f}"
        )

    exponent = growth_exponent(lengths, elapsed_seconds)
    largest_ratio = elapsed_seconds[-1] / elapsed_seconds[-2]
    length_ratio = lengths[-1] / lengths[-2]
    print(
        f"growth_exponent={exponent:.6f} "
        f"last_time_ratio={largest_ratio:.6f} "
        f"last_length_ratio={length_ratio:.6f}"
    )
    if exponent > args.max_exponent:
        print(
            f"窗口增长性能门禁失败：{exponent:.6f} > {args.max_exponent:.6f}",
            file=sys.stderr,
        )
        return 20
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
