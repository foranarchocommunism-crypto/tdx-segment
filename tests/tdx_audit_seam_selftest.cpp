#include "segment_audit.hpp"

#if defined(NDEBUG)
#undef NDEBUG
#endif
#include <cassert>
#include <array>
#include <future>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace {
using namespace tdx_segment;
using namespace tdx_segment::detail;

void PointText(std::ostream& out, const std::optional<Point>& point) {
    if (point) { out << point->bar << ',' << point->price; }
    else { out << "null"; }
    out << ';';
}

void SegmentText(std::ostream& out, const SegmentResult& segment) {
    out << static_cast<int>(segment.direction) << ';' << static_cast<int>(segment.status)
        << ';' << segment.start.bar << ',' << segment.start.price << ';';
    PointText(out, segment.endpoint);
    PointText(out, segment.intrinsic_confirmed_at);
    PointText(out, segment.confirmed_at);
    PointText(out, segment.next_segment_t_not_before);
    out << (segment.end_case ? static_cast<int>(*segment.end_case) : -1) << ';'
        << static_cast<int>(segment.wait_reason) << ';'
        << static_cast<int>(segment.recovery_diagnostic) << ';';
    for (const auto& point : segment.explicitly_rejected_ts) {
        out << point.bar << ',' << point.price << ':';
    }
    out << ';' << segment.recovery_evidence.has_value() << ';';
    if (segment.recovery_evidence) {
        const auto& evidence = *segment.recovery_evidence;
        out << static_cast<int>(evidence.wait_reason) << ';'
            << (evidence.wait_case ? static_cast<int>(*evidence.wait_case) : -1) << ';';
        for (const Point& point : {evidence.s, evidence.t, evidence.r,
                evidence.reverse_intrinsic_at, evidence.reverse_visible_at, evidence.effective_at}) {
            out << point.bar << ',' << point.price << ';';
        }
        out << evidence.observed_pens << ';' << evidence.stagnant_pens << ';'
            << evidence.residence_pens << ';' << static_cast<int>(evidence.reverse_case) << ';';
    }
}

std::string ChainText(const AlternatingSegmentsResult& chain) {
    std::ostringstream out;
    out << std::setprecision(17);
    for (const auto& segment : chain.confirmed_segments) { SegmentText(out, segment); }
    out << '|' << chain.unresolved_segment.has_value() << '|';
    if (chain.unresolved_segment) { SegmentText(out, *chain.unresolved_segment); }
    out << chain.next_start.bar << ',' << chain.next_start.price << ';'
        << static_cast<int>(chain.next_direction) << ';';
    PointText(out, chain.next_segment_t_not_before);
    return out.str();
}

struct CheckingSink final : AuditSink {
    std::size_t started{}, requested{}, completed{};
    void OnEvaluationStep(const AuditEvaluation& evaluation, AuditStep step) override {
        assert(evaluation.depth > 0);
        assert(evaluation.scope_last >= evaluation.scope_start);
        if (step == AuditStep::EvaluationStarted) { ++started; } else { ++requested; }
    }
    void OnEvaluationCompleted(const AuditEvaluation& evaluation, const SegmentResult& result) override {
        assert(evaluation.scope_start == result.start.bar);
        assert(evaluation.direction == result.direction);
        ++completed;
    }
};

void EvaluationText(std::ostream& out, const AuditEvaluation& evaluation) {
    out << evaluation.root_start << ',' << static_cast<int>(evaluation.root_direction)
        << ',' << evaluation.depth << ',' << evaluation.scope_start << ',' << evaluation.scope_last
        << ',' << static_cast<int>(evaluation.direction) << ',' << evaluation.candidate_floor
        << ',' << static_cast<int>(evaluation.role) << ',' << evaluation.recovery_query << ';';
}

struct RecordingSink final : AuditSink {
    std::ostringstream trace;
    std::size_t started{}, completed{}, requests{}, recovery_queries{};

    RecordingSink() { trace << std::setprecision(17); }

    void OnEvaluationStep(const AuditEvaluation& evaluation, AuditStep step) override {
        assert(evaluation.depth > 0 && evaluation.scope_last >= evaluation.scope_start);
        trace << 'S' << static_cast<int>(step) << ':';
        EvaluationText(trace, evaluation);
        if (step == AuditStep::EvaluationStarted) {
            ++started;
            if (evaluation.role == AuditRole::RecoveryPrefix) { ++recovery_queries; }
        } else {
            assert(step == AuditStep::NeedReaction);
            ++requests;
        }
    }

    void OnPrimaryObservation(const AuditEvaluation& evaluation, const PrimaryAuditObservation& value) override {
        trace << "P:";
        EvaluationText(trace, evaluation);
        trace << value.t.bar << ',' << value.t.price << ';' << value.first_pen << ',' << value.last_pen
            << ';' << value.first_bar << ',' << value.last_bar << ';' << value.low << ',' << value.high << ';';
    }

    void OnSecondaryDecision(const AuditEvaluation& evaluation, const SecondaryAuditDecision& value) override {
        trace << "D:";
        EvaluationText(trace, evaluation);
        trace << value.scope_start << ';' << value.t.bar << ',' << value.t.price
            << ';' << static_cast<int>(value.status) << ';' << static_cast<int>(value.decision_role) << ';';
        PointText(trace, value.confirmed_at);
        PointText(trace, value.successor);
    }

    void OnCase2Cancellation(const AuditEvaluation& evaluation, const Case2AuditCancellation& value) override {
        trace << "C:";
        EvaluationText(trace, evaluation);
        trace << value.t.bar << ',' << value.t.price << ';' << value.successor.bar << ',' << value.successor.price << ';';
        PointText(trace, value.at);
    }

    void OnEvaluationCompleted(const AuditEvaluation& evaluation, const SegmentResult& result) override {
        assert(evaluation.scope_start == result.start.bar && evaluation.direction == result.direction);
        trace << "R:";
        EvaluationText(trace, evaluation);
        SegmentText(trace, result);
        ++completed;
    }
};

struct InputScratch {
    std::vector<float> prices, markers;

    explicit InputScratch(std::size_t maximum) {
        prices.reserve(maximum);
        markers.reserve(maximum);
    }

    void Load(const std::vector<float>& source, bool mirrored) {
        assert(source.size() >= 2U && prices.capacity() >= source.size() && markers.capacity() >= source.size());
        const float* old_prices = prices.empty() ? nullptr : prices.data();
        const float* old_markers = markers.empty() ? nullptr : markers.data();
        prices.assign(source.begin(), source.end());
        markers.resize(source.size());
        if (mirrored) { for (float& value : prices) { value = -value; } }
        markers[0U] = prices[1U] > prices[0U] ? -1.0F : 1.0F;
        for (std::size_t i = 1U; i < prices.size(); ++i) {
            markers[i] = prices[i] > prices[i - 1U] ? 1.0F : -1.0F;
        }
        // Every call in the mixed sequence deliberately reuses the same raw
        // addresses. Snapshot ownership, rather than an input pointer key,
        // must determine the result and observation lifetime.
        assert(old_prices == nullptr || old_prices == prices.data());
        assert(old_markers == nullptr || old_markers == markers.data());
    }
};

struct CapturedSnapshot {
    std::string text;
    std::size_t requests{}, recovery_queries{};
};

CapturedSnapshot Capture(const std::vector<float>& source, bool mirrored,
    const LongStallPolicy& policy, InputScratch& scratch) {
    scratch.Load(source, mirrored);
    const auto decoded = DecodePens(TdxSeriesView{static_cast<std::int32_t>(scratch.prices.size()),
        scratch.markers.data(), scratch.prices.data(), scratch.prices.data()});
    assert(decoded.ok());
    assert(decoded.snapshot.Pens().size() + 1U == scratch.prices.size());
    const auto initial = FindEarliestSegmentStart(decoded.snapshot);
    assert(initial.has_value());
    const auto plain = JudgeAlternatingSegments(decoded.snapshot, initial->start_pos, initial->direction, policy);
    RecordingSink sink;
    const auto observed = JudgeAlternatingSegmentsAudited(decoded.snapshot,
        initial->start_pos, initial->direction, policy, sink);
    assert(ChainText(plain) == ChainText(observed));
    assert(sink.started == sink.completed && sink.completed > 0U);
    return {ChainText(observed) + '#' + sink.trace.str(), sink.requests, sink.recovery_queries};
}

void CheckIndependentSnapshotLifetimes(const std::vector<float>& recovery) {
    const std::vector<float> nested{0,5,2,20,1,6,3,10,2,5,3,7,1};
    auto changed = nested;
    // Preserve both raw length and decoded Pen count. A changed endpoint that
    // folds two same-kind markers would miss a cache keyed by Pen count.
    changed.back() = 4.0F;
    const std::vector<std::vector<float>> inputs{nested, changed,
        {0,10,4,12,2,8,-2,6,-4,4,-6,2,-8,0}, recovery};
    struct Scenario {
        std::size_t input;
        bool mirrored;
        LongStallPolicy policy;
        std::string expected;
    };
    const std::array<LongStallPolicy, 3U> policies{LongStallPolicy{},
        DefaultLongStallPolicy(true), LongStallPolicy{true, 43, 13, 9, true}};
    std::vector<Scenario> cases;
    std::size_t nested_requests = 0U, recovery_queries = 0U;
    for (std::size_t input = 0U; input < inputs.size(); ++input) {
        for (const bool mirrored : {false, true}) {
            for (const auto& policy : policies) {
                // Each expected record is captured with fresh raw buffers,
                // before exercising any interleaved or concurrent sequence.
                InputScratch independent(recovery.size());
                auto captured = Capture(inputs[input], mirrored, policy, independent);
                nested_requests += captured.requests;
                recovery_queries += captured.recovery_queries;
                cases.push_back({input, mirrored, policy, std::move(captured.text)});
            }
        }
    }
    assert(inputs[0U].size() == inputs[1U].size());
    for (std::size_t variant = 0U; variant < 6U; ++variant) {
        assert(cases[variant].expected != cases[variant + 6U].expected);
    }
    assert(nested_requests > 0U);
    InputScratch mixed(recovery.size());
    for (std::size_t round = 0U; round < 3U; ++round) {
        for (std::size_t offset = 0U; offset < cases.size(); ++offset) {
            const std::size_t index = round % 2U == 0U ? offset : cases.size() - 1U - offset;
            const auto& item = cases[index];
            assert(Capture(inputs[item.input], item.mirrored, item.policy, mixed).text == item.expected);
        }
    }
    std::vector<std::future<void>> tasks;
    for (std::size_t worker = 0U; worker < 8U; ++worker) {
        tasks.push_back(std::async(std::launch::async, [&inputs, &cases, &recovery, worker]() {
            InputScratch scratch(recovery.size());
            for (std::size_t offset = 0U; offset < cases.size() * 2U; ++offset) {
                const auto& item = cases[(offset + worker * 5U) % cases.size()];
                assert(Capture(inputs[item.input], item.mirrored, item.policy, scratch).text == item.expected);
            }
        }));
    }
    for (auto& task : tasks) { task.get(); }
    std::cout << "{\"independent_snapshot_scenarios\":" << cases.size()
        << ",\"same_length_changed_tail_variants\":6,\"interleaved_replays\":" << cases.size() * 3U
        << ",\"same_decoded_pen_count\":true"
        << ",\"concurrent_workers\":8,\"concurrent_replays\":" << cases.size() * 16U
        << ",\"nested_requests_in_independent_records\":" << nested_requests
        << ",\"recovery_queries_in_independent_records\":" << recovery_queries
        << ",\"complete_audit_callbacks_compared\":true}\n";
}

std::size_t Check(const std::vector<float>& source, bool mirrored) {
    std::vector<float> prices(source), markers(source.size());
    if (mirrored) { for (float& value : prices) { value = -value; } }
    markers[0] = prices[1] > prices[0] ? -1.0F : 1.0F;
    for (std::size_t i = 1; i < prices.size(); ++i) {
        markers[i] = prices[i] > prices[i - 1] ? 1.0F : -1.0F;
    }
    std::size_t completions = 0;
    for (std::size_t length = 4; length <= prices.size(); length += 3) {
        const auto decoded = DecodePens(TdxSeriesView{static_cast<std::int32_t>(length),
            markers.data(), prices.data(), prices.data()});
        assert(decoded.ok());
        const auto initial = FindEarliestSegmentStart(decoded.snapshot);
        if (!initial) { continue; }
        for (const auto policy : {LongStallPolicy{}, DefaultLongStallPolicy(true),
                LongStallPolicy{true, 43, 13, 9, true}}) {
            const auto expected = JudgeAlternatingSegments(decoded.snapshot, initial->start_pos,
                initial->direction, policy);
            CheckingSink sink;
            const auto actual = JudgeAlternatingSegmentsAudited(decoded.snapshot, initial->start_pos,
                initial->direction, policy, sink);
            assert(ChainText(actual) == ChainText(expected));
            assert(sink.completed > 0 && sink.started >= sink.completed);
            completions += sink.completed;
        }
    }
    return completions;
}
}  // namespace

int main() {
    std::vector<float> recovery{900,1050,970,1100,950,1000,900,1000,940,990,950,998};
    for (int i = 0; i < 80; ++i) {
        recovery.push_back(810.0F + static_cast<float>(i % 7));
        recovery.push_back(870.0F + static_cast<float>(i % 9));
    }
    const std::vector<float> ordinary{0,10,4,12,2,8,-2,6,-4,4,-6,2,-8,0};
    assert(Check(ordinary, false) > 0);
    assert(Check(ordinary, true) > 0);
    std::vector<std::future<std::size_t>> calls;
    for (int i = 0; i < 8; ++i) {
        calls.push_back(std::async(std::launch::async, [&recovery, i]() {
            return Check(recovery, (i % 2) != 0);
        }));
    }
    for (auto& call : calls) { assert(call.get() > 0); }
    CheckIndependentSnapshotLifetimes(recovery);
}
