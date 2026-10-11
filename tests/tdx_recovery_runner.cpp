#include "segment_engine.hpp"
#include <cstdint>
#include <exception>
#include <iostream>
#include <string>
#include <vector>

int main(int argc, char** argv) {
    using namespace tdx_segment;
    using namespace tdx_segment::detail;
    LongStallPolicy policy{true, 43, 13, 9, true};
    if (argc == 4) {
        policy.length = std::stoi(argv[1]);
        policy.stagnation = std::stoi(argv[2]);
        policy.residence = std::stoi(argv[3]);
    }
    try {
        std::int32_t cases{};
        if (!(std::cin >> cases)) {return 1;}
        while (cases-- > 0) {
            std::int32_t n{};
            if (!(std::cin >> n) || n <= 0) {return 2;}
            std::vector<float> markers(static_cast<std::size_t>(n)), highs(markers), lows(markers);
            for (auto* series : {&markers, &highs, &lows}) {
                for (auto& value : *series) {if (!(std::cin >> value)) {return 3;}}
            }
            const auto decoded = DecodePens({n, markers.data(), highs.data(), lows.data()});
            if (!decoded.ok()) {std::cout << "E\n"; continue;}
            const auto initial = FindEarliestSegmentStart(decoded.snapshot);
            if (!initial.has_value()) {std::cout << "N\n"; continue;}
            const auto chain = JudgeAlternatingSegments(decoded.snapshot, initial->start_pos, initial->direction, policy);
            std::cout << "C " << chain.confirmed_segments.size();
            for (const auto& s : chain.confirmed_segments) {
                std::cout << ' ' << static_cast<int>(s.direction) << ' ' << s.start.bar << ' ' << s.endpoint->bar
                    << ' ' << s.intrinsic_confirmed_at->bar << ' ' << s.confirmed_at->bar << ' ' << static_cast<int>(*s.end_case)
                    << ' ' << (s.next_segment_t_not_before.has_value() ? s.next_segment_t_not_before->bar : -1);
            }
            std::cout << ' ' << (chain.unresolved_segment.has_value() ? chain.unresolved_segment->start.bar : -1);
            std::size_t count = 0;
            for (const auto& s : chain.confirmed_segments) {if (s.end_case == EndCase::LongStallRecovery) {++count;}}
            std::cout << ' ' << count;
            for (const auto& s : chain.confirmed_segments) {
                if (s.end_case != EndCase::LongStallRecovery) {continue;}
                const auto& e = *s.recovery_evidence;
                std::cout << ' ' << static_cast<int>(e.wait_reason) << ' ' << (e.wait_case.has_value() ? static_cast<int>(*e.wait_case) : -1)
                    << ' ' << e.s.bar << ' ' << e.t.bar << ' ' << e.r.bar << ' ' << e.observed_pens
                    << ' ' << e.stagnant_pens << ' ' << e.residence_pens << ' ' << static_cast<int>(e.reverse_case)
                    << ' ' << e.reverse_intrinsic_at.bar << ' ' << e.reverse_visible_at.bar << ' ' << e.effective_at.bar;
            }
            std::cout << '\n';
        }
    } catch (const std::exception& e) {std::cerr << e.what() << '\n'; return 4;}
    return 0;
}
