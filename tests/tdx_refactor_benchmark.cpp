// Build the identical source against each frozen/candidate core. No DLL install.
#include "tdx_segment/tdx_segment.hpp"

#include <Windows.h>
#include <Psapi.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>

namespace allocation_probe {
#if defined(TDX_MEASURE_ALLOCATIONS) && TDX_MEASURE_ALLOCATIONS
inline constexpr bool kInstrumented = true;
#else
inline constexpr bool kInstrumented = false;
#endif
struct Meter {
    std::uint64_t generation{}, allocations{}, bytes{}, live{}, peak{};
    bool enabled{};
};
thread_local Meter meter;
struct alignas(std::max_align_t) Header {
    void* base;
    const Meter* owner;
    std::size_t size;
    std::uint64_t generation;
};
void* Allocate(std::size_t size, std::size_t alignment) {
    alignment = (std::max)(alignment, alignof(Header));
    if (size > (std::numeric_limits<std::size_t>::max)() - sizeof(Header) - alignment) {
        throw std::bad_alloc();
    }
    void* base = std::malloc(size + sizeof(Header) + alignment);
    if (base == nullptr) { throw std::bad_alloc(); }
    const auto begin = reinterpret_cast<std::uintptr_t>(base) + sizeof(Header);
    const auto aligned = (begin + alignment - 1U) & ~(alignment - 1U);
    auto* header = reinterpret_cast<Header*>(aligned) - 1;
    header->base = base;
    header->owner = &meter;
    header->size = size;
    header->generation = meter.enabled ? meter.generation : 0U;
    if (meter.enabled) {
        ++meter.allocations;
        meter.bytes += size;
        meter.live += size;
        meter.peak = (std::max)(meter.peak, meter.live);
    }
    return reinterpret_cast<void*>(aligned);
}
void Free(void* address) noexcept {
    if (address == nullptr) { return; }
    const auto* header = reinterpret_cast<const Header*>(address) - 1;
    if (header->owner == &meter && header->generation != 0U && header->generation == meter.generation) {
        meter.live -= header->size;
    }
    std::free(header->base);
}
void Begin() noexcept {
    const auto generation = meter.generation + 1U;
    meter = Meter{};
    meter.generation = generation;
    meter.enabled = kInstrumented;
}
}  // namespace allocation_probe

#if defined(TDX_MEASURE_ALLOCATIONS) && TDX_MEASURE_ALLOCATIONS
void* operator new(std::size_t n) { return allocation_probe::Allocate(n, alignof(std::max_align_t)); }
void* operator new[](std::size_t n) { return ::operator new(n); }
void operator delete(void* p) noexcept { allocation_probe::Free(p); }
void operator delete[](void* p) noexcept { allocation_probe::Free(p); }
void operator delete(void* p, std::size_t) noexcept { allocation_probe::Free(p); }
void operator delete[](void* p, std::size_t) noexcept { allocation_probe::Free(p); }
void* operator new(std::size_t n, std::align_val_t a) { return allocation_probe::Allocate(n, static_cast<std::size_t>(a)); }
void* operator new[](std::size_t n, std::align_val_t a) { return ::operator new(n, a); }
void operator delete(void* p, std::align_val_t) noexcept { allocation_probe::Free(p); }
void operator delete[](void* p, std::align_val_t) noexcept { allocation_probe::Free(p); }
void operator delete(void* p, std::size_t, std::align_val_t) noexcept { allocation_probe::Free(p); }
void operator delete[](void* p, std::size_t, std::align_val_t) noexcept { allocation_probe::Free(p); }
void* operator new(std::size_t n, const std::nothrow_t&) noexcept { try { return ::operator new(n); } catch (...) { return nullptr; } }
void* operator new[](std::size_t n, const std::nothrow_t&) noexcept { try { return ::operator new[](n); } catch (...) { return nullptr; } }
void operator delete(void* p, const std::nothrow_t&) noexcept { allocation_probe::Free(p); }
void operator delete[](void* p, const std::nothrow_t&) noexcept { allocation_probe::Free(p); }
void* operator new(std::size_t n, std::align_val_t a, const std::nothrow_t&) noexcept { try { return ::operator new(n, a); } catch (...) { return nullptr; } }
void* operator new[](std::size_t n, std::align_val_t a, const std::nothrow_t&) noexcept { try { return ::operator new[](n, a); } catch (...) { return nullptr; } }
void operator delete(void* p, std::align_val_t, const std::nothrow_t&) noexcept { allocation_probe::Free(p); }
void operator delete[](void* p, std::align_val_t, const std::nothrow_t&) noexcept { allocation_probe::Free(p); }
#endif

namespace {
using Clock = std::chrono::steady_clock;
struct CaseData { std::vector<float> markers, highs, lows; };
struct Options {
    std::string scenario{"daily"}, window{"append"}, policy{"1"}, input;
    std::size_t length{8192U}, max_length{16384U}, case_index{}, samples{30U}, warmups{3U}, calls_per_sample{1U};
    bool server{};
};
std::size_t Positive(const char* text) {
    const auto value = std::stoull(text);
    if (value > static_cast<unsigned long long>((std::numeric_limits<std::int32_t>::max)())) {
        throw std::invalid_argument("argument exceeds int32 range");
    }
    return static_cast<std::size_t>(value);
}
Options Parse(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string name(argv[i]);
        if (name == "--server") { options.server = true; continue; }
        if (i + 1 >= argc) { throw std::invalid_argument("missing option value"); }
        const char* value = argv[++i];
        if (name == "--scenario") { options.scenario = value; }
        else if (name == "--window") { options.window = value; }
        else if (name == "--policy") { options.policy = value; }
        else if (name == "--input") { options.input = value; }
        else if (name == "--length") { options.length = Positive(value); }
        else if (name == "--max-length") { options.max_length = Positive(value); }
        else if (name == "--case-index") { options.case_index = Positive(value); }
        else if (name == "--samples") { options.samples = Positive(value); }
        else if (name == "--warmups") { options.warmups = Positive(value); }
        else if (name == "--calls-per-sample") { options.calls_per_sample = Positive(value); }
        else { throw std::invalid_argument("unknown option: " + name); }
    }
    if (options.length < 3U || options.warmups < 3U || options.samples < 30U) {
        throw std::invalid_argument("length >= 3, warmups >= 3 and samples >= 30 required");
    }
    if (options.policy != "1" && options.policy != "2" && options.policy != "OFF") {
        throw std::invalid_argument("policy must be 1, 2 or OFF");
    }
    if (options.window != "append" && options.window != "prepend") {
        throw std::invalid_argument("window must be append or prepend");
    }
    if (options.calls_per_sample == 0U || options.calls_per_sample > 1024U
        || (allocation_probe::kInstrumented && options.calls_per_sample != 1U)) {
        throw std::invalid_argument("calls per sample must be 1..1024; allocation probes require 1");
    }
    return options;
}
CaseData BuildSynthetic(const Options& options) {
    // Existing reference nested_trend_a_pens, with interpolation between pivots.
    constexpr std::array<float, 13U> nested{0,5,2,20,1,6,3,10,2,5,3,7,1};
    constexpr std::array<float, 8U> shorts{0,10,4,12,2,8,-2,6};
    constexpr std::array<float, 12U> prelude{900,1050,970,1100,950,1000,900,1000,940,990,950,998};
    constexpr std::array<float, 8U> stall{820,880,815,875,810,870,825,885};
    constexpr std::array<float, 8U> containment{0,20,5,18,7,16,5,20};
    const bool windowed = options.scenario == "daily" || options.scenario == "window";
    const auto total = windowed ? (std::max)(options.length, options.max_length) : options.length;
    CaseData data;
    data.markers.resize(total);
    data.highs.resize(total);
    data.lows.resize(total);
    for (std::size_t i = 0; i < total; ++i) {
        float price{}, marker{};
        if (options.scenario == "nested") {
            const auto numerator = i * (nested.size() - 1U);
            const auto denominator = total - 1U;
            const auto left = numerator / denominator;
            const auto remainder = numerator % denominator;
            const auto right = (std::min)(left + 1U, nested.size() - 1U);
            const auto ratio = static_cast<float>(remainder) / static_cast<float>(denominator);
            price = nested[left] + (nested[right] - nested[left]) * ratio;
            // Endpoint markers are placed at the first bar reaching each pivot.
            if (i == 0U) { marker = -1.0F; }
            else if (left != ((i - 1U) * (nested.size() - 1U)) / denominator) {
                price = nested[left];
                marker = nested[left] > nested[left - 1U] ? 1.0F : -1.0F;
            }
        } else if (windowed) {
            const auto pivot = i / 4U;
            const auto next = pivot + 1U;
            const float start = shorts[pivot % shorts.size()] + static_cast<float>(pivot / 64U);
            const float end = shorts[next % shorts.size()] + static_cast<float>(next / 64U);
            const float ratio = static_cast<float>(i % 4U) / 4.0F;
            price = start + (end - start) * ratio;
            marker = i % 4U == 0U ? (pivot % 2U == 0U ? -1.0F : 1.0F) : 0.0F;
        } else {
            if (options.scenario == "many-short") { price = shorts[i % shorts.size()]; }
            else if (options.scenario == "stall") { price = i < prelude.size() ? prelude[i] : stall[(i - prelude.size()) % stall.size()]; }
            else if (options.scenario == "longtrend") { price = static_cast<float>(i / 2U) * 2.0F + (i % 2U == 0U ? 0.0F : 3.0F); }
            else if (options.scenario == "containment-equal") { price = containment[i % containment.size()]; }
            else { throw std::invalid_argument("unknown synthetic scenario"); }
            marker = i == 0U ? -1.0F : (price > data.highs[i - 1U] ? 1.0F : -1.0F);
        }
        data.highs[i] = price;
        data.lows[i] = price;
        data.markers[i] = marker;
    }
    if (total > options.length) {
        const auto offset = options.window == "prepend" ? total - options.length : 0U;
        for (auto* series : {&data.markers, &data.highs, &data.lows}) {
            std::vector<float> slice(series->begin() + static_cast<std::ptrdiff_t>(offset),
                                     series->begin() + static_cast<std::ptrdiff_t>(offset + options.length));
            *series = std::move(slice);
        }
    }
    return data;
}
CaseData ReadInput(const Options& options) {
    std::ifstream stream(options.input);
    if (!stream) { throw std::runtime_error("cannot read input file"); }
    std::size_t count{};
    if (!(stream >> count) || options.case_index >= count) { throw std::runtime_error("invalid input case count/index"); }
    CaseData data;
    for (std::size_t index = 0; index <= options.case_index; ++index) {
        std::size_t n{};
        if (!(stream >> n) || n > static_cast<std::size_t>((std::numeric_limits<std::int32_t>::max)())) {
            throw std::runtime_error("invalid input length");
        }
        for (auto* series : {&data.markers, &data.highs, &data.lows}) {
            series->resize(index == options.case_index ? n : 0U);
            for (std::size_t j = 0; j < n; ++j) {
                float value{};
                if (!(stream >> value)) { throw std::runtime_error("truncated input"); }
                if (index == options.case_index) { (*series)[j] = value; }
            }
        }
    }
    return data;
}
tdx_segment::CalculationResult Run(const CaseData& data, const std::string& policy) {
    const tdx_segment::TdxSeriesView view{static_cast<std::int32_t>(data.markers.size()),
                                        data.markers.data(), data.highs.data(), data.lows.data()};
    if (policy == "OFF") { return tdx_segment::CalculateSegmentEventsWithoutRecovery(view); }
    return policy == "2" ? tdx_segment::CalculateSegmentEventsV2(view) : tdx_segment::CalculateSegmentEventsV1(view);
}
PROCESS_MEMORY_COUNTERS Memory() {
    PROCESS_MEMORY_COUNTERS result{};
    result.cb = sizeof(result);
    if (!GetProcessMemoryInfo(GetCurrentProcess(), &result, sizeof(result))) {
        throw std::runtime_error("GetProcessMemoryInfo failed");
    }
    return result;
}
void Sample(const CaseData& data, const Options& options, std::size_t index) {
    const auto before = Memory();
    double seconds{};
    std::uint64_t checksum = 1469598103934665603ULL;
    allocation_probe::Begin();
    std::uint64_t expected_checksum{};
    for (std::size_t call = 0U; call < options.calls_per_sample; ++call) {
        const auto started = Clock::now();
        const auto result = Run(data, options.policy);
        const auto stopped = Clock::now();
        seconds += std::chrono::duration<double>(stopped - started).count();
        allocation_probe::meter.enabled = false;
        if (!result.ok()) { throw std::runtime_error("benchmark calculation rejected input"); }
        checksum = 1469598103934665603ULL;
        for (const auto value : result.event_mask) {
            checksum = (checksum ^ static_cast<std::uint64_t>(value)) * 1099511628211ULL;
        }
        if (call != 0U && checksum != expected_checksum) {
            throw std::runtime_error("repeated calculation changed its checksum");
        }
        expected_checksum = checksum;
    }
    seconds /= static_cast<double>(options.calls_per_sample);
    const auto allocation = allocation_probe::meter;
    const auto after = Memory();
    std::cout << "{\"kind\":\"sample\",\"index\":" << index
              << ",\"seconds\":" << seconds << ",\"checksum\":" << checksum
              << ",\"calls_per_sample\":" << options.calls_per_sample
              << ",\"allocations\":" << allocation.allocations << ",\"allocated_bytes\":" << allocation.bytes
              << ",\"peak_live_bytes\":" << allocation.peak << ",\"remaining_live_bytes\":" << allocation.live
              << ",\"working_set_before\":" << before.WorkingSetSize
              << ",\"working_set_after\":" << after.WorkingSetSize
              << ",\"process_peak_working_set\":" << after.PeakWorkingSetSize << "}\n" << std::flush;
}
}  // namespace

int main(int argc, char** argv) {
    try {
        const auto options = Parse(argc, argv);
        const auto parse_started = Clock::now();
        const auto data = options.input.empty() ? BuildSynthetic(options) : ReadInput(options);
        const auto parse_seconds = std::chrono::duration<double>(Clock::now() - parse_started).count();
        for (std::size_t i = 0; i < options.warmups; ++i) {
            if (!Run(data, options.policy).ok()) { throw std::runtime_error("warmup rejected input"); }
        }
        std::cout << std::setprecision(17)
                  << "{\"kind\":\"ready\",\"bars\":" << data.markers.size()
                  << ",\"pointer_bits\":" << sizeof(void*) * 8U
                  << ",\"parse_seconds\":" << parse_seconds
                  << ",\"warmups\":" << options.warmups
                  << ",\"calls_per_sample\":" << options.calls_per_sample
                  << ",\"instrumented\":" << (allocation_probe::kInstrumented ? "true" : "false")
                  << ",\"allocation_probe\":\""
                  << (allocation_probe::kInstrumented ? "thread_local_global_new_with_generation_headers" : "disabled")
                  << "\"}\n" << std::flush;
        if (options.server) {
            std::string command;
            std::size_t index{};
            while (std::getline(std::cin, command)) {
                if (command == "QUIT") { break; }
                if (command != "SAMPLE") { throw std::invalid_argument("server accepts SAMPLE or QUIT"); }
                Sample(data, options, index++);
            }
        } else {
            for (std::size_t i = 0; i < options.samples; ++i) { Sample(data, options, i); }
        }
        return 0;
    } catch (const std::exception& error) {
        allocation_probe::meter.enabled = false;
        std::cerr << "Benchmark failed: " << error.what() << '\n';
        return 1;
    }
}
