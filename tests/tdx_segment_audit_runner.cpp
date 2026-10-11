#include "segment_engine.hpp"

#include <cstdint>
#include <string>
#include <exception>
#include <iostream>
#include <iomanip>
#include <optional>
#include <vector>

namespace {

bool ReadSeries(std::vector<float>& values, std::int32_t length) {
    values.resize(static_cast<std::size_t>(length));
    for (float& value : values) {
        if (!(std::cin >> value)) {
            return false;
        }
    }
    return true;
}

std::int32_t Bar(const std::optional<tdx_segment::detail::Point>& point) {
    return point.has_value() ? point->bar : -1;
}

int EndCaseValue(
    const std::optional<tdx_segment::detail::EndCase>& end_case
) {
    return end_case.has_value() ? static_cast<int>(*end_case) : -1;
}

void WriteSegment(const tdx_segment::detail::SegmentResult& segment) {
    std::cout
        << ' ' << static_cast<int>(segment.direction)
        << ' ' << static_cast<int>(segment.status)
        << ' ' << segment.start.bar
        << ' ' << Bar(segment.endpoint)
        << ' ' << Bar(segment.intrinsic_confirmed_at)
        << ' ' << Bar(segment.confirmed_at)
        << ' ' << EndCaseValue(segment.end_case)
        << ' ' << Bar(segment.next_segment_t_not_before);
}

void WritePoint(const tdx_segment::detail::Point& point) {
    std::cout << '[' << point.bar << ',' << point.price << ']';
}

void WritePoint(const std::optional<tdx_segment::detail::Point>& point) {
    if (point) WritePoint(*point);
    else std::cout << "null";
}

void WriteFullSegment(const tdx_segment::detail::SegmentResult& segment) {
    std::cout << "{\"direction\":" << static_cast<int>(segment.direction)
        << ",\"status\":" << static_cast<int>(segment.status) << ",\"start\":";
    WritePoint(segment.start);
    std::cout << ",\"endpoint\":";
    WritePoint(segment.endpoint);
    std::cout << ",\"intrinsic_confirmed_at\":";
    WritePoint(segment.intrinsic_confirmed_at);
    std::cout << ",\"confirmed_at\":";
    WritePoint(segment.confirmed_at);
    std::cout << ",\"end_case\":" << EndCaseValue(segment.end_case)
        << ",\"next_segment_t_not_before\":";
    WritePoint(segment.next_segment_t_not_before);
    std::cout << ",\"explicitly_rejected_ts\":[";
    for (std::size_t i = 0; i < segment.explicitly_rejected_ts.size(); ++i) {
        if (i) std::cout << ',';
        WritePoint(segment.explicitly_rejected_ts[i]);
    }
    std::cout << "],\"wait_reason\":" << static_cast<int>(segment.wait_reason)
        << ",\"recovery_diagnostic\":" << static_cast<int>(segment.recovery_diagnostic)
        << ",\"recovery_evidence\":";
    if (segment.recovery_evidence) {
        const auto& e = *segment.recovery_evidence;
        std::cout << "{\"wait_reason\":" << static_cast<int>(e.wait_reason)
            << ",\"wait_case\":" << EndCaseValue(e.wait_case) << ",\"s\":";
        WritePoint(e.s);
        std::cout << ",\"t\":"; WritePoint(e.t);
        std::cout << ",\"r\":"; WritePoint(e.r);
        std::cout << ",\"observed_pens\":" << e.observed_pens
            << ",\"stagnant_pens\":" << e.stagnant_pens
            << ",\"residence_pens\":" << e.residence_pens
            << ",\"reverse_case\":" << static_cast<int>(e.reverse_case)
            << ",\"reverse_intrinsic_at\":";
        WritePoint(e.reverse_intrinsic_at);
        std::cout << ",\"reverse_visible_at\":"; WritePoint(e.reverse_visible_at);
        std::cout << ",\"effective_at\":"; WritePoint(e.effective_at);
        std::cout << '}';
    } else std::cout << "null";
    std::cout << '}';
}

void WriteFullChain(const tdx_segment::detail::InitialSegmentStart& initial,
    const tdx_segment::detail::AlternatingSegmentsResult& chain) {
    std::cout << "{\"error\":0,\"initial\":{\"start_pos\":" << initial.start_pos
        << ",\"direction\":" << static_cast<int>(initial.direction) << ",\"start\":";
    WritePoint(initial.start);
    std::cout << "},\"confirmed\":[";
    for (std::size_t i = 0; i < chain.confirmed_segments.size(); ++i) {
        if (i) std::cout << ',';
        WriteFullSegment(chain.confirmed_segments[i]);
    }
    std::cout << "],\"unresolved\":";
    if (chain.unresolved_segment) WriteFullSegment(*chain.unresolved_segment);
    else std::cout << "null";
    std::cout << ",\"next_start\":"; WritePoint(chain.next_start);
    std::cout << ",\"next_direction\":" << static_cast<int>(chain.next_direction)
        << ",\"next_segment_t_not_before\":";
    WritePoint(chain.next_segment_t_not_before);
    std::cout << "}\n";
}

}  // namespace

int main(int argc, char** argv) {
    bool recovery = false, baseline = false, full_json = false;
    for (int i = 1; i < argc; ++i) {
        const std::string arg(argv[i]);
        if (arg == "--recovery") { recovery = true; baseline = false; }
        else if (arg == "--baseline") { baseline = true; recovery = false; }
        else if (arg == "--full-json") full_json = true;
        else return 1;
    }
    std::cout << std::setprecision(17);
    using namespace tdx_segment;
    using namespace tdx_segment::detail;

    std::int32_t case_count = 0;
    if (!(std::cin >> case_count) || case_count < 0) {
        return 1;
    }
    try {
        for (std::int32_t case_index = 0; case_index < case_count; ++case_index) {
            std::int32_t data_len = 0;
            if (!(std::cin >> data_len) || data_len < 0) {
                return 2;
            }
            std::vector<float> markers;
            std::vector<float> highs;
            std::vector<float> lows;
            if (
                !ReadSeries(markers, data_len)
                || !ReadSeries(highs, data_len)
                || !ReadSeries(lows, data_len)
            ) {
                return 3;
            }

            const DecodeResult decoded = DecodePens(TdxSeriesView{
                data_len,
                markers.data(),
                highs.data(),
                lows.data(),
            });
            if (!decoded.ok()) {
                if (full_json) {
                    std::cout << "{\"error\":" << static_cast<int>(decoded.error)
                        << ",\"error_bar\":" << decoded.error_bar << "}\n";
                    continue;
                }
                std::cout << "E " << static_cast<int>(decoded.error)
                          << ' ' << decoded.error_bar << '\n';
                continue;
            }
            const auto initial = FindEarliestSegmentStart(decoded.snapshot);
            if (!initial.has_value()) {
                if (full_json) {
                    std::cout << "{\"error\":0,\"initial\":null,\"confirmed\":[],\"unresolved\":null}\n";
                    continue;
                }
                std::cout << "N\n";
                continue;
            }

            const AlternatingSegmentsResult chain = JudgeAlternatingSegments(
                decoded.snapshot,
                initial->start_pos,
                initial->direction,
                baseline ? LongStallPolicy{} : recovery ? LongStallPolicy{true, 43, 13, 9, true} : DefaultLongStallPolicy(true)
            );
            if (full_json) {
                WriteFullChain(*initial, chain);
                continue;
            }
            std::cout << "C " << initial->start_pos
                      << ' ' << static_cast<int>(initial->direction)
                      << ' ' << chain.confirmed_segments.size();
            for (const SegmentResult& segment : chain.confirmed_segments) {
                WriteSegment(segment);
            }
            std::cout << ' ' << (chain.unresolved_segment.has_value() ? 1 : 0);
            if (chain.unresolved_segment.has_value()) {
                WriteSegment(*chain.unresolved_segment);
            }
            std::cout << ' ' << chain.next_start.bar
                      << ' ' << static_cast<int>(chain.next_direction)
                      << ' ' << Bar(chain.next_segment_t_not_before)
                      << '\n';
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 4;
    }
    return 0;
}
