#include "long_stall_recovery.hpp"
#include "segment_evaluator.hpp"
#include "feature_sequences.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <tuple>
#include <utility>
#include <vector>

namespace tdx_segment::detail {
namespace {

[[nodiscard]] bool PointsEqual(const Point& left, const Point& right) noexcept {
    return left.bar == right.bar && std::abs(left.price - right.price) <= kPriceEpsilon;
}

[[nodiscard]] Direction OppositeDirection(Direction direction) noexcept {
    return direction == Direction::Up ? Direction::Down : Direction::Up;
}

[[nodiscard]] bool HasStrictSegmentPriceDirection(
    Direction direction, const Point& start, const Point& endpoint
) noexcept {
    return direction == Direction::Up
        ? endpoint.price > start.price + kPriceEpsilon
        : endpoint.price < start.price - kPriceEpsilon;
}

[[nodiscard]] SegmentResult ApplyChainConfirmationFloor(
    const SegmentResult& result, const std::optional<Point>& previous_confirmed_at
) {
    if (result.status != SegmentStatus::Confirmed || !result.confirmed_at.has_value()) {
        throw std::logic_error("Only a confirmed segment can receive a chain floor.");
    }
    if (!previous_confirmed_at.has_value()
        || result.confirmed_at->bar >= previous_confirmed_at->bar) {
        return result;
    }
    SegmentResult adjusted = result;
    adjusted.confirmed_at = previous_confirmed_at;
    return adjusted;
}

struct RecoveryMonitor {
    std::int32_t stagnant{};
    std::int32_t resident{};
    Point visible{};
};

class RecoveryPrefixOracle {
public:
    explicit RecoveryPrefixOracle(const PenRange& pens, AuditSink* audit_sink)
        : pens_(pens), audit_sink_(audit_sink) {}

    const SegmentResult& Result(
        std::int32_t start, Direction direction, std::int32_t count,
        const std::optional<Point>& floor = std::nullopt
    ) {
        const auto key = std::make_tuple(start, direction, count, floor.has_value() ? floor->bar : -1);
        const auto found = cache_.find(key);
        if (found != cache_.end()) {
            return found->second;
        }
        const PenRange local = pens_.Subrange(
            static_cast<std::size_t>(start), static_cast<std::size_t>(count)
        );
        auto result = EvaluateOneSegment(
            local, 0, direction, floor, audit_sink_, AuditRole::RecoveryPrefix
        );
        return cache_.emplace(key, std::move(result)).first->second;
    }

    std::optional<std::int32_t> FirstConfirmed(
        std::int32_t start, Direction direction, std::int32_t maximum,
        const std::optional<Point>& floor = std::nullopt
    ) {
        const auto key = std::make_tuple(start, direction, maximum, floor.has_value() ? floor->bar : -1);
        const auto found = first_.find(key);
        if (found != first_.end()) {
            return found->second;
        }
        std::optional<std::int32_t> answer;
        if (maximum >= start + 3 && Result(start, direction, maximum, floor).status == SegmentStatus::Confirmed) {
            auto lo = start + 3;
            auto hi = maximum;
            while (lo < hi) {
                const auto mid = lo + (hi - lo) / 2;
                if (Result(start, direction, mid, floor).status == SegmentStatus::Confirmed) {
                    hi = mid;
                } else {
                    lo = mid + 1;
                }
            }
            answer = lo;
        }
        first_.emplace(key, answer);
        return answer;
    }

private:
    PenRange pens_;
    AuditSink* audit_sink_{};
    using Key = std::tuple<std::int32_t, Direction, std::int32_t, std::int32_t>;
    std::map<Key, SegmentResult> cache_;
    std::map<Key, std::optional<std::int32_t>> first_;
};

}  // namespace

[[nodiscard]] std::optional<std::pair<SegmentResult, SegmentResult>> TryLongStallRecovery(
    const PenRange& pens, std::int32_t start_pos, Direction direction,
    const std::optional<Point>& floor, const std::optional<Point>& effective_floor,
    const LongStallPolicy& policy, RecoveryDiagnostic& diagnostic,
    AuditSink* audit_sink
) {
    diagnostic = RecoveryDiagnostic::TooShort;
    const PenRange local = pens.Subrange(static_cast<std::size_t>(start_pos), pens.size());

    const auto maximum = static_cast<std::int32_t>(local.size()) - (policy.last_pen_unfinished ? 1 : 0);
    if (maximum <= policy.length || !ValidatePenRange(local)) {
        return std::nullopt;
    }
    if (!ThreePensDefineSegment(local, 0, direction)) {diagnostic = RecoveryDiagnostic::SeedNotReady; return std::nullopt;}
    diagnostic = RecoveryDiagnostic::NoStallResidence;
    const double sign = direction == Direction::Up ? 1.0 : -1.0;
    const Point s = local.front().start;
    double favorable = sign * s.price;
    std::int32_t record = 0;
    std::int32_t resident = 0;
    std::optional<std::int32_t> run_start;
    std::vector<std::pair<std::int32_t, std::int32_t>> runs;
    std::vector<RecoveryMonitor> monitors(static_cast<std::size_t>(maximum + 1));
    std::vector<bool> qualified(static_cast<std::size_t>(maximum + 1), false);
    bool breakthrough = false;
    bool progress = false;
    for (std::int32_t count = 1; count <= maximum; ++count) {
        const auto& pen = local[static_cast<std::size_t>(count - 1)];
        const double extreme = sign == 1.0 ? PenIntrinsicHigh(pen) : -PenIntrinsicLow(pen);

        if (extreme > favorable + kPriceEpsilon) {
            favorable = extreme;
            record = count;
        }
        resident = extreme < sign * s.price - kPriceEpsilon ? resident + 1 : 0;
        const auto visible = policy.last_pen_unfinished ? local[static_cast<std::size_t>(count)].end : pen.end;
        const auto stagnant = count - record;
        monitors[static_cast<std::size_t>(count)] = {stagnant, resident, visible};
        if (count >= 3 && count % 2 == 1) {
            breakthrough = breakthrough || sign * pen.end.price > sign * local.front().end.price + kPriceEpsilon;
            const auto k = count - 3;
            const auto& first = local[static_cast<std::size_t>(k)];
            const auto& second = local[static_cast<std::size_t>(k + 1)];
            progress = progress || (ThreePensDefineSegment(local, k, direction)
                && sign * second.end.price >= sign * first.start.price - kPriceEpsilon
                && sign * pen.end.price > sign * first.end.price + kPriceEpsilon);
            qualified[static_cast<std::size_t>(count)] = count == 3
                ? ThreePensMeetStrictInitialProgress(local, 0, direction) : breakthrough || progress;
        }
        const bool gate = count > policy.length && stagnant >= policy.stagnation && resident >= policy.residence
            && (!effective_floor.has_value() || visible.bar >= effective_floor->bar);
        if (gate && !run_start.has_value()) {
            run_start = count;
        }
        if (!gate && run_start.has_value()) {
            runs.emplace_back(*run_start, count - 1);
            run_start.reset();
        }
    }
    if (run_start.has_value()) {
        runs.emplace_back(*run_start, maximum);
    }
    if (runs.empty()) {
        return std::nullopt;
    }
    diagnostic = RecoveryDiagnostic::NormalPriority;
    RecoveryPrefixOracle oracle(local, audit_sink);
    auto normal_visible = oracle.FirstConfirmed(0, direction, maximum + (policy.last_pen_unfinished ? 1 : 0), floor);
    if (normal_visible.has_value()) {*normal_visible -= policy.last_pen_unfinished ? 1 : 0;}
    for (const auto& run : runs) {
        const auto begin = run.first;
        const auto end = normal_visible.has_value() ? std::min(run.second, *normal_visible - 1) : run.second;
        if (begin > end) {
            continue;
        }
        diagnostic = RecoveryDiagnostic::NoLegalT;
        std::map<std::int32_t, std::int32_t> certificates;
        std::set<std::int32_t> checkpoints{begin};
        for (std::int32_t q = 3; q < begin; q += 2) {
            const auto t = local[static_cast<std::size_t>(q - 1)].end;
            if (!qualified[static_cast<std::size_t>(q)] || !HasStrictSegmentPriceDirection(direction, s, t)
                || (floor.has_value() && t.bar < floor->bar)) {
                continue;
            }
            diagnostic = RecoveryDiagnostic::ReverseCertificateInsufficient;
            const auto first = oracle.FirstConfirmed(q, OppositeDirection(direction), end);
            if (!first.has_value()) {
                continue;
            }
            const auto& reverse = oracle.Result(q, OppositeDirection(direction), *first);
            if (!HasStrictSegmentPriceDirection(OppositeDirection(direction), s, *reverse.endpoint)) {
                continue;
            }
            certificates.emplace(q, *first);
            checkpoints.insert(std::max(begin, *first));
        }
        for (const auto count : checkpoints) {
            const auto& pending = oracle.Result(0, direction, count + (policy.last_pen_unfinished ? 1 : 0), floor);
            if (pending.status != SegmentStatus::Pending) {
                break;
            }
            std::vector<std::int32_t> eligible;
            double peak = -std::numeric_limits<double>::infinity();
            for (const auto& entry : certificates) {
                const auto q = entry.first;
                const auto t = local[static_cast<std::size_t>(q - 1)].end;
                if (entry.second > count || (pending.endpoint.has_value() && !PointsEqual(t, *pending.endpoint))
                    || std::any_of(pending.explicitly_rejected_ts.begin(), pending.explicitly_rejected_ts.end(),
                        [&t](const Point& rejected) {return PointsEqual(t, rejected);})) {
                    continue;
                }
                eligible.push_back(q);
                peak = std::max(peak, sign * t.price);
            }
            std::optional<std::int32_t> chosen;
            for (auto q : eligible) {
                if (sign * local[static_cast<std::size_t>(q - 1)].end.price >= peak - kPriceEpsilon) {
                    chosen = q; // ordered by q; global EPS ties choose the later one
                }
            }
            if (!chosen.has_value()) {
                if (pending.endpoint.has_value()) {diagnostic = RecoveryDiagnostic::ActiveCandidateBlocked;}
                continue;
            }
            auto reverse = oracle.Result(*chosen, OppositeDirection(direction), count);
            if (reverse.status != SegmentStatus::Confirmed
                || !HasStrictSegmentPriceDirection(OppositeDirection(direction), s, *reverse.endpoint)) {
                continue;
            }
            const auto& monitor = monitors[static_cast<std::size_t>(count)];
            LongStallEvidence evidence;
            evidence.wait_reason = pending.wait_reason;
            evidence.wait_case = pending.end_case;
            evidence.s = s;
            evidence.t = local[static_cast<std::size_t>(*chosen - 1)].end;
            evidence.r = *reverse.endpoint;
            evidence.observed_pens = count;
            evidence.stagnant_pens = monitor.stagnant;
            evidence.residence_pens = monitor.resident;
            evidence.reverse_case = *reverse.end_case;
            evidence.reverse_intrinsic_at = *reverse.intrinsic_confirmed_at;
            evidence.reverse_visible_at = monitors[static_cast<std::size_t>(certificates.at(*chosen))].visible;
            evidence.effective_at = monitor.visible;
            SegmentResult old;
            old.direction = direction;
            old.status = SegmentStatus::Confirmed;
            old.start = s;
            old.endpoint = evidence.t;
            old.intrinsic_confirmed_at = monitor.visible;
            old.confirmed_at = monitor.visible;
            old.end_case = EndCase::LongStallRecovery;
            diagnostic = RecoveryDiagnostic::Recovered;
            old.recovery_diagnostic = diagnostic;
            old.recovery_evidence = evidence;
            reverse.recovery_diagnostic = RecoveryDiagnostic::Recovered;
            reverse.recovery_evidence = evidence;
            reverse = ApplyChainConfirmationFloor(reverse, monitor.visible);
            return std::make_pair(old, reverse);
        }
    }
    return std::nullopt;
}

}  // namespace tdx_segment::detail
