#include "tdx_segment/tdx_segment.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
bool recovery_benchmark = false;
bool stalled_benchmark = false;

struct CaseData {
    std::vector<float> markers;
    std::vector<float> highs;
    std::vector<float> lows;
};

CaseData BuildManyShortSegments(std::int32_t data_len) {
    const std::array<float, 8U> cycle{
        0.0F,
        10.0F,
        4.0F,
        12.0F,
        2.0F,
        8.0F,
        -2.0F,
        6.0F,
    };
    CaseData data;
    data.markers.resize(static_cast<std::size_t>(data_len));
    data.highs.resize(static_cast<std::size_t>(data_len));
    data.lows.resize(static_cast<std::size_t>(data_len));
    for (std::int32_t bar = 0; bar < data_len; ++bar) {
        const float price = cycle[static_cast<std::size_t>(bar) % cycle.size()];
        data.highs[static_cast<std::size_t>(bar)] = price;
        data.lows[static_cast<std::size_t>(bar)] = price;
        if (bar == 0) {
            data.markers[0] = -1.0F;
            continue;
        }
        const float previous = data.highs[static_cast<std::size_t>(bar - 1)];
        data.markers[static_cast<std::size_t>(bar)] =
            price > previous ? 1.0F : -1.0F;
    }
    return data;
}

CaseData BuildStalledRecovery(std::int32_t data_len) {
    const std::array<float, 12U> prelude{900,1050,970,1100,950,1000,900,1000,940,990,950,998};
    const std::array<float, 8U> cycle{820,880,815,875,810,870,825,885};
    CaseData data;
    data.highs.resize(static_cast<std::size_t>(data_len));
    data.lows.resize(data.highs.size());
    data.markers.resize(data.highs.size());
    for (std::int32_t i=0; i<data_len; ++i) {
        const auto j=static_cast<std::size_t>(i);
        const float price=j<prelude.size()?prelude[j]:cycle[(j-prelude.size())%cycle.size()];
        data.highs[j]=price;data.lows[j]=price;
        data.markers[j]=i==0?-1.0F:price>data.highs[j-1U]?1.0F:-1.0F;
    }
    return data;
}

tdx_segment::CalculationResult Run(const CaseData& data) {
    return (recovery_benchmark ? tdx_segment::CalculateSegmentEventsV2 : tdx_segment::CalculateSegmentEventsV1)(
        tdx_segment::TdxSeriesView{
            static_cast<std::int32_t>(data.markers.size()),
            data.markers.data(),
            data.highs.data(),
            data.lows.data(),
        }
    );
}

double MedianSeconds(const CaseData& data, std::size_t repetitions) {
    // 构造、文本 I/O、进程启动和结果打印都在计时之外。
    const auto warmup = Run(data);
    if (!warmup.ok()) {
        throw std::runtime_error("Benchmark warmup rejected a valid series.");
    }

    std::vector<double> samples;
    samples.reserve(repetitions);
    std::uint64_t checksum = 0U;
    for (std::size_t repetition = 0; repetition < repetitions; ++repetition) {
        const auto started = Clock::now();
        const auto result = Run(data);
        const auto stopped = Clock::now();
        if (!result.ok()) {
            throw std::runtime_error("Benchmark run rejected a valid series.");
        }
        samples.push_back(std::chrono::duration<double>(stopped - started).count());
        checksum += std::accumulate(
            result.event_mask.begin(),
            result.event_mask.end(),
            std::uint64_t{0U},
            [](std::uint64_t sum, float value) {
                return sum + static_cast<std::uint64_t>(value);
            }
        );
    }
    if (checksum == 0U) {
        throw std::runtime_error("Benchmark output unexpectedly contains no events.");
    }
    std::sort(samples.begin(), samples.end());
    return samples[samples.size() / 2U];
}

double GrowthExponent(
    const std::array<std::int32_t, 4U>& lengths,
    const std::array<double, 4U>& seconds
) {
    std::array<double, 4U> xs{};
    std::array<double, 4U> ys{};
    for (std::size_t index = 0; index < lengths.size(); ++index) {
        xs[index] = std::log(static_cast<double>(lengths[index]));
        ys[index] = std::log(seconds[index]);
    }
    const double x_mean = std::accumulate(xs.begin(), xs.end(), 0.0)
        / static_cast<double>(xs.size());
    const double y_mean = std::accumulate(ys.begin(), ys.end(), 0.0)
        / static_cast<double>(ys.size());
    double numerator = 0.0;
    double denominator = 0.0;
    for (std::size_t index = 0; index < xs.size(); ++index) {
        numerator += (xs[index] - x_mean) * (ys[index] - y_mean);
        denominator += (xs[index] - x_mean) * (xs[index] - x_mean);
    }
    return numerator / denominator;
}

double MedianDoublingExponent(
    const std::array<std::int32_t, 4U>& lengths,
    const std::array<double, 4U>& seconds
) {
    std::array<double, 3U> adjacent{};
    for (std::size_t index = 1; index < lengths.size(); ++index) {
        adjacent[index - 1U] = std::log(seconds[index] / seconds[index - 1U])
            / std::log(
                static_cast<double>(lengths[index])
                / static_cast<double>(lengths[index - 1U])
            );
    }
    std::sort(adjacent.begin(), adjacent.end());
    return adjacent[adjacent.size() / 2U];
}

}  // namespace

int main(int argc, char** argv) {
    stalled_benchmark = argc > 1 && std::string(argv[1]) == "--stall";
    recovery_benchmark = argc > 1 && std::string(argv[1]) == "--recovery";
    try {
        constexpr std::array<std::int32_t, 4U> lengths{
            32768,
            65536,
            131072,
            262144,
        };
        constexpr std::size_t repetitions = 5U;
        std::array<double, lengths.size()> elapsed{};
        for (std::size_t index = 0; index < lengths.size(); ++index) {
            const CaseData data = stalled_benchmark ? BuildStalledRecovery(lengths[index]) : BuildManyShortSegments(lengths[index]);
            elapsed[index] = MedianSeconds(data, repetitions);
            std::cout << "length=" << lengths[index]
                      << " seconds_per_call=" << elapsed[index] << '\n';
        }
        const double exponent = GrowthExponent(lengths, elapsed);
        const double median_doubling_exponent = MedianDoublingExponent(
            lengths,
            elapsed
        );
        std::cout << "growth_exponent=" << exponent << '\n';
        std::cout << "median_doubling_exponent="
                  << median_doubling_exponent << '\n';
        // Windows 调度或实时防护可能只污染一个规模。三个相邻翻倍区间
        // 的中位指数仍能拒绝持续的平方退化，同时不会让单点停顿误报。
        if (median_doubling_exponent > 1.35) {
            std::cerr << "Many-short-segments growth gate failed: "
                      << median_doubling_exponent << " > 1.35\n";
            return 20;
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Benchmark failed: " << error.what() << '\n';
        return 1;
    }
}
