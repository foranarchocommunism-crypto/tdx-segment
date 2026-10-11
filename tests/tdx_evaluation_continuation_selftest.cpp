#include "segment_evaluator.hpp"

#if defined(NDEBUG)
#undef NDEBUG
#endif
#include <algorithm>
#include <cassert>
#include <future>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <tuple>
#include <vector>

namespace {
using namespace tdx_segment::detail;

struct FrameSink final : AuditSink {
    using Identity = std::tuple<std::int32_t, std::int32_t, std::int32_t,
        Direction, std::int32_t, AuditRole>;
    std::set<Identity> started;
    std::size_t requests{}, completions{};

    void OnEvaluationStep(const AuditEvaluation& scope, AuditStep step) override {
        const Identity identity{scope.depth, scope.scope_start, scope.scope_last,
            scope.direction, scope.candidate_floor, scope.role};
        if (step == AuditStep::EvaluationStarted) {
            assert(started.insert(identity).second);
        } else {
            assert(step == AuditStep::NeedReaction);
            assert(started.count(identity) == 1U);
            ++requests;
        }
    }
    void OnEvaluationCompleted(const AuditEvaluation& scope, const SegmentResult& result) override {
        assert(scope.scope_start == result.start.bar);
        assert(scope.direction == result.direction);
        ++completions;
    }
};

std::vector<Pen> Pens(const std::vector<double>& prices, bool mirrored) {
    std::vector<Pen> pens;
    for (std::size_t i = 1U; i < prices.size(); ++i) {
        const double sign = mirrored ? -1.0 : 1.0;
        Pen pen;
        pen.position = static_cast<std::int32_t>(i - 1U);
        pen.start = Point{pen.position, prices[i - 1U] * sign};
        pen.end = Point{static_cast<std::int32_t>(i), prices[i] * sign};
        pen.direction = pen.end.price > pen.start.price ? Direction::Up : Direction::Down;
        pens.push_back(pen);
    }
    return pens;
}

std::string Fingerprint(const SegmentResult& result) {
    std::ostringstream out;
    out << std::setprecision(17) << static_cast<int>(result.direction) << ';'
        << static_cast<int>(result.status) << ';' << result.start.bar << ',' << result.start.price;
    for (const auto& point : {result.endpoint, result.intrinsic_confirmed_at,
            result.confirmed_at, result.next_segment_t_not_before}) {
        out << ';';
        if (point) { out << point->bar << ',' << point->price; }
        else { out << "null"; }
    }
    out << ';' << (result.end_case ? static_cast<int>(*result.end_case) : -1)
        << ';' << static_cast<int>(result.wait_reason)
        << ';' << static_cast<int>(result.recovery_diagnostic)
        << ';' << result.recovery_evidence.has_value();
    for (const Point& point : result.explicitly_rejected_ts) {
        out << ';' << point.bar << ',' << point.price;
    }
    return out.str();
}

EvaluationMetrics Check(const std::vector<double>& prices, bool mirrored,
    const std::optional<Point>& floor = std::nullopt) {
    const auto pens = Pens(prices, mirrored);
    const Direction direction = mirrored ? Direction::Down : Direction::Up;
    const auto expected = EvaluateOneSegment(PenRange(pens), 0, direction, floor);
    EvaluationMetrics metrics;
    FrameSink sink;
    const auto actual = EvaluateOneSegment(PenRange(pens), 0, direction, floor,
        &sink, AuditRole::Formal, &metrics);
    assert(Fingerprint(actual) == Fingerprint(expected));
    assert(metrics.frames_created == sink.started.size());
    assert(metrics.frames_created == sink.completions);
    assert(metrics.reaction_requests == sink.requests);
    assert(metrics.reaction_resumptions == metrics.reaction_requests);
    assert(metrics.primary_appends == metrics.candidates_prepared);
    assert(metrics.parent_candidate_replays == 0U);
    assert(metrics.parent_event_replays == 0U);
    return metrics;
}
}  // namespace

int main() {
    // Frozen Python/C++ regression: the outer T remains bar 3, while a nested
    // Trend-A reaction confirms at bar 12. It forces genuine child requests.
    const std::vector<double> nested{0,5,2,20,1,6,3,10,2,5,3,7,1};
    EvaluationMetrics total;
    for (const bool mirrored : {false, true}) {
        const auto pens = Pens(nested, mirrored);
        const auto result = EvaluateOneSegment(PenRange(pens), 0,
            mirrored ? Direction::Down : Direction::Up, std::nullopt);
        assert(result.status == SegmentStatus::Confirmed);
        assert(result.end_case == EndCase::TrendA);
        assert(result.endpoint && result.endpoint->bar == 3);
        assert(result.endpoint->price == (mirrored ? -20.0 : 20.0));
        assert(result.intrinsic_confirmed_at && result.intrinsic_confirmed_at->bar == 12);
        assert(result.confirmed_at && result.confirmed_at->bar == 12);
        assert(result.next_segment_t_not_before && result.next_segment_t_not_before->bar == 12);
        const auto metrics = Check(nested, mirrored);
        assert(metrics.reaction_requests > 0U);
        assert(metrics.maximum_depth >= 2U);
        total.frames_created += metrics.frames_created;
        total.reaction_requests += metrics.reaction_requests;
        total.reaction_resumptions += metrics.reaction_resumptions;
        total.primary_appends += metrics.primary_appends;
        total.candidates_prepared += metrics.candidates_prepared;
        total.parent_candidate_replays += metrics.parent_candidate_replays;
        total.parent_event_replays += metrics.parent_event_replays;
        total.maximum_depth = (std::max)(total.maximum_depth, metrics.maximum_depth);
        for (std::size_t count = 4U; count <= nested.size(); ++count) {
            const std::vector<double> prefix(nested.begin(), nested.begin() + count);
            Check(prefix, mirrored);
            Check(prefix, mirrored, Point{3, mirrored ? -20.0 : 20.0});
        }
        auto changed_tail = nested;
        changed_tail.back() = 4.0;
        Check(changed_tail, mirrored);
        Check(nested, mirrored);
    }
    std::vector<std::future<EvaluationMetrics>> tasks;
    for (int i = 0; i < 8; ++i) {
        tasks.push_back(std::async(std::launch::async, [&nested, i]() {
            auto input = nested;
            if ((i % 3) == 0) { input.back() = 4.0; }
            return Check(input, (i % 2) != 0);
        }));
    }
    for (auto& task : tasks) { assert(task.get().reaction_requests > 0U); }
    std::cout << "{\"frames_created\":" << total.frames_created
        << ",\"reaction_requests\":" << total.reaction_requests
        << ",\"same_candidate_event_resumptions\":" << total.reaction_resumptions
        << ",\"primary_appends\":" << total.primary_appends
        << ",\"candidates_prepared\":" << total.candidates_prepared
        << ",\"parent_candidate_replays\":" << total.parent_candidate_replays
        << ",\"parent_event_replays\":" << total.parent_event_replays << ",\"maximum_depth\":"
        << total.maximum_depth << "}\n";
}
