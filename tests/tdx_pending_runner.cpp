#include "segment_audit.hpp"

#include <array>
#include <exception>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

#ifndef TDX_PENDING_SECOND_FORMAT
#define TDX_PENDING_SECOND_FORMAT 0
#endif
#ifndef TDX_PENDING_DEFAULT_RECOVERY
#define TDX_PENDING_DEFAULT_RECOVERY 0
#endif
#ifndef TDX_PENDING_DEFAULT_RESIDENCE
#define TDX_PENDING_DEFAULT_RESIDENCE 9
#endif

namespace {
using namespace tdx_segment::detail;
using EvaluationKey = std::tuple<int, int, int, int, int, int, int, int, bool>;

EvaluationKey Key(const AuditEvaluation& e) {
    return {e.root_start, static_cast<int>(e.root_direction), e.depth,
        e.scope_start, e.scope_last, static_cast<int>(e.direction),
        e.candidate_floor, static_cast<int>(e.role), e.recovery_query};
}

int Bar(const std::optional<Point>& p) { return p.has_value() ? p->bar : -1; }

// Chain floors and recovery installation change the visible clock and recovery
// fields. Structural and intrinsic evidence identifies the underlying result.
std::string ResultKey(const SegmentResult& s) {
    std::ostringstream out;
    out << std::setprecision(17) << static_cast<int>(s.direction) << ':'
        << static_cast<int>(s.status) << ':' << s.start.bar << ':' << s.start.price;
    const auto point = [&out](const std::optional<Point>& p) {
        if (p) out << ':' << p->bar << ':' << p->price;
        else out << ":null";
    };
    point(s.endpoint);
    point(s.intrinsic_confirmed_at);
    point(s.next_segment_t_not_before);
    out << ':' << (s.end_case ? static_cast<int>(*s.end_case) : -1)
        << ':' << static_cast<int>(s.wait_reason);
    for (const auto& p : s.explicitly_rejected_ts) out << ':' << p.bar << ':' << p.price;
    return out.str();
}

class ReportSink final : public AuditSink {
public:
    std::vector<std::array<double, 15>> observations;
    std::vector<std::array<int, 9>> second;
    std::map<std::string, std::vector<std::array<int, 3>>> result_cancels;

    void OnEvaluationStep(const AuditEvaluation& e, AuditStep step) override {
        if (step == AuditStep::EvaluationStarted) frame_cancels_[Key(e)].clear();
    }

    void OnPrimaryObservation(const AuditEvaluation& e, const PrimaryAuditObservation& o) override {
        if (e.recovery_query) return;
        const std::array<double, 15> row{{
            static_cast<double>(e.root_start), static_cast<double>(e.root_direction),
            static_cast<double>(e.depth), static_cast<double>(e.scope_start),
            static_cast<double>(e.scope_last), static_cast<double>(e.direction),
            static_cast<double>(o.t.bar), static_cast<double>(o.first_pen),
            static_cast<double>(o.last_pen), static_cast<double>(o.first_bar),
            static_cast<double>(o.last_bar), static_cast<double>(e.candidate_floor),
            o.t.price, o.low, o.high,
        }};
        if (observation_keys_.insert(row).second) observations.push_back(row);
    }

    void OnSecondaryDecision(const AuditEvaluation& e, const SecondaryAuditDecision& d) override {
        if (e.recovery_query) return;
        const std::array<int, 9> row{{e.root_start, static_cast<int>(e.root_direction),
            e.depth, d.scope_start, d.t.bar, static_cast<int>(d.status),
            Bar(d.confirmed_at), Bar(d.successor), d.decision_role}};
        if (second_keys_.insert(row).second) second.push_back(row);
    }

    void OnCase2Cancellation(const AuditEvaluation& e, const Case2AuditCancellation& c) override {
        frame_cancels_[Key(e)].push_back({c.t.bar, c.successor.bar, Bar(c.at)});
    }

    void OnEvaluationCompleted(const AuditEvaluation& e, const SegmentResult& s) override {
        if (e.role == AuditRole::NestedReaction) return;
        auto found = frame_cancels_.find(Key(e));
        if (found == frame_cancels_.end()) return;
        result_cancels[ResultKey(s)] = found->second;
    }

private:
    std::set<std::array<double, 15>> observation_keys_;
    std::set<std::array<int, 9>> second_keys_;
    std::map<EvaluationKey, std::vector<std::array<int, 3>>> frame_cancels_;
};

bool Read(std::vector<float>& values, int length) {
    values.resize(static_cast<std::size_t>(length));
    for (auto& value : values) if (!(std::cin >> value)) return false;
    return true;
}

template <typename T, std::size_t N>
void WriteRow(const std::array<T, N>& row) {
    std::cout << '[';
    for (std::size_t i = 0; i < N; ++i) {
        if (i) std::cout << ',';
        std::cout << row[i];
    }
    std::cout << ']';
}
}  // namespace

int main(int argc, char** argv) {
    using namespace tdx_segment;
    using namespace tdx_segment::detail;
    bool recovery = TDX_PENDING_DEFAULT_RECOVERY != 0;
    bool second_format = TDX_PENDING_SECOND_FORMAT != 0;
    int residence = TDX_PENDING_DEFAULT_RESIDENCE;
    for (int i = 1; i < argc; ++i) {
        const std::string arg(argv[i]);
        if (arg == "--baseline") recovery = false;
        else if (arg == "--recovery") recovery = true;
        else if (arg == "--second") second_format = true;
        else if (arg == "--primary") second_format = false;
        else if (arg == "--residence" && i + 1 < argc) residence = std::stoi(argv[++i]);
        else return 1;
    }
    int count{};
    if (!(std::cin >> count) || count < 0) return 1;
    std::cout << std::setprecision(17);
    try {
        for (int ci = 0; ci < count; ++ci) {
            int n{};
            if (!(std::cin >> n) || n <= 0) return 2;
            std::vector<float> m, h, l;
            if (!Read(m, n) || !Read(h, n) || !Read(l, n)) return 3;
            const auto decoded = DecodePens(TdxSeriesView{n, m.data(), h.data(), l.data()});
            if (!decoded.ok()) throw std::logic_error("Invalid assessment input");
            const auto initial = FindEarliestSegmentStart(decoded.snapshot);
            ReportSink sink;
            AlternatingSegmentsResult chain;
            std::vector<float> events(static_cast<std::size_t>(n), 0.0F);
            if (initial) {
                chain = JudgeAlternatingSegmentsAudited(decoded.snapshot, initial->start_pos,
                    initial->direction, LongStallPolicy{recovery, 43, 13, residence, true}, sink);
                auto encoded = EncodeSegmentEvents(n, FlattenConfirmedSegments(n, chain.confirmed_segments));
                if (!encoded.valid) throw std::logic_error("Invalid observed event encoding");
                events = std::move(encoded.event_mask);
            }
            std::cout << "{\"events\":[";
            bool first = true;
            for (int i = 0; i < n; ++i) {
                if (second_format && events[static_cast<std::size_t>(i)] == 0.0F) continue;
                if (!first) std::cout << ',';
                first = false;
                if (second_format) std::cout << '[' << i << ',';
                std::cout << events[static_cast<std::size_t>(i)];
                if (second_format) std::cout << ']';
            }
            if (!second_format) {
                std::cout << "],\"observations\":[";
                first = true;
                for (const auto& row : sink.observations) {
                    if (!first) std::cout << ',';
                    first = false;
                    WriteRow(row);
                }
                std::cout << "],\"chain\":[";
                first = true;
                for (const auto& s : chain.confirmed_segments) {
                    if (!first) std::cout << ',';
                    first = false;
                    WriteRow(std::array<int, 5>{{s.start.bar, s.endpoint->bar,
                        static_cast<int>(s.direction), static_cast<int>(*s.end_case), s.confirmed_at->bar}});
                }
                std::cout << "]}\n";
                continue;
            }
            std::cout << "],\"second\":[";
            first = true;
            for (const auto& row : sink.second) {
                if (!first) std::cout << ',';
                first = false;
                WriteRow(row);
            }
            std::cout << "],\"cancels\":[";
            first = true;
            const auto print_cancels = [&sink, &first](const SegmentResult& s) {
                const auto found = sink.result_cancels.find(ResultKey(s));
                if (found == sink.result_cancels.end()) return;
                for (const auto& c : found->second) {
                    if (!first) std::cout << ',';
                    first = false;
                    WriteRow(std::array<int, 5>{{s.start.bar, static_cast<int>(s.direction), c[0], c[1], c[2]}});
                }
            };
            for (const auto& s : chain.confirmed_segments) print_cancels(s);
            if (chain.unresolved_segment) print_cancels(*chain.unresolved_segment);
            std::cout << "],\"pending\":";
            if (chain.unresolved_segment) {
                const auto& s = *chain.unresolved_segment;
                WriteRow(std::array<int, 4>{{s.start.bar, Bar(s.endpoint),
                    static_cast<int>(s.direction), static_cast<int>(s.wait_reason)}});
            } else std::cout << "null";
            std::cout << "}\n";
        }
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 4;
    }
    return 0;
}
