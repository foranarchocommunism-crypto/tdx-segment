#include "segment_engine.hpp"
#include "segment_evaluator.hpp"
#include "long_stall_recovery.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace tdx_segment::detail {
namespace {
[[noreturn]] void InvariantFailure(const char* message) {
    throw std::logic_error(message);
}

void Require(bool condition, const char* message) {
    if (!condition) {
        InvariantFailure(message);
    }
}

[[nodiscard]] bool AlmostEqual(double left, double right) noexcept {
    return std::abs(left - right) <= kPriceEpsilon;
}

[[nodiscard]] bool PointsEqual(
    const Point& left,
    const Point& right
) noexcept {
    return left.bar == right.bar && AlmostEqual(left.price, right.price);
}

[[nodiscard]] Direction OppositeDirection(Direction direction) noexcept {
    return direction == Direction::Up ? Direction::Down : Direction::Up;
}

[[nodiscard]] bool HasStrictSegmentPriceDirection(
    Direction direction,
    const Point& start,
    const Point& endpoint
) noexcept {
    return direction == Direction::Up
        ? endpoint.price > start.price + kPriceEpsilon
        : endpoint.price < start.price - kPriceEpsilon;
}

[[nodiscard]] std::optional<std::int32_t> FindPenStartingAtPoint(
    const PenRange& pens, const Point& point, Direction required_direction
) {
    std::size_t begin = 0U, end = pens.size();
    while (begin < end) {
        const std::size_t middle = begin + (end - begin) / 2U;
        if (pens[middle].start.bar < point.bar) { begin = middle + 1U; }
        else { end = middle; }
    }
    if (begin == pens.size()) { return std::nullopt; }
    const Pen match = pens[begin];
    if (match.direction != required_direction || !PointsEqual(match.start, point)) {
        return std::nullopt;
    }
    return match.position;
}

void ValidateAdjacentSegments(
    const SegmentResult& previous,
    const SegmentResult& current
) {
    Require(
        previous.status == SegmentStatus::Confirmed
            && current.status == SegmentStatus::Confirmed,
        "An alternating chain may contain only confirmed segments."
    );
    Require(
        previous.endpoint.has_value()
            && current.endpoint.has_value()
            && previous.confirmed_at.has_value()
            && current.confirmed_at.has_value(),
        "Confirmed chain segments require endpoint and confirmation."
    );
    Require(
        HasStrictSegmentPriceDirection(
            previous.direction,
            previous.start,
            *previous.endpoint
        ) && HasStrictSegmentPriceDirection(
            current.direction,
            current.start,
            *current.endpoint
        ),
        "Confirmed segment semantics must match strict net price direction."
    );
    Require(
        current.direction == OppositeDirection(previous.direction),
        "Segment directions must alternate."
    );
    Require(
        PointsEqual(*previous.endpoint, current.start),
        "Adjacent segments must share the exact structural endpoint."
    );
    Require(
        current.confirmed_at->bar >= previous.confirmed_at->bar,
        "Chain-level confirmation bars must be nondecreasing."
    );
}

void ValidateAlternatingChain(const std::vector<SegmentResult>& segments) {
    for (const SegmentResult& segment : segments) {
        Require(
            segment.status == SegmentStatus::Confirmed
                && segment.endpoint.has_value()
                && segment.confirmed_at.has_value(),
            "An alternating chain may contain only complete confirmed segments."
        );
        Require(
            HasStrictSegmentPriceDirection(
                segment.direction,
                segment.start,
                *segment.endpoint
            ),
            "Confirmed segment semantics must match strict net price direction."
        );
    }
    for (std::size_t index = 1; index < segments.size(); ++index) {
        ValidateAdjacentSegments(
            segments[index - 1U],
            segments[index]
        );
    }
}

[[nodiscard]] AlternatingSegmentsResult JudgeAlternatingSegmentsImpl(
    const PenRange& pens,
    std::int32_t start_pos,
    Direction initial_direction,
    const LongStallPolicy& policy,
    AuditSink* audit_sink = nullptr
) {
    if (policy.enabled) {
        Require(policy.length > 0 && policy.stagnation > 0 && policy.residence > 0,
                "Recovery thresholds must be positive.");
    }
    Require(!pens.empty(), "Alternating segment engine requires pens.");
    Require(start_pos >= 0 && start_pos < static_cast<std::int32_t>(pens.size()),
            "Alternating segment start position is out of range.");

    AlternatingSegmentsResult chain;
    if (!ValidatePenRange(pens)) {
        SegmentResult invalid;
        invalid.direction = initial_direction;
        invalid.status = SegmentStatus::InvalidInput;
        invalid.start = pens[static_cast<std::size_t>(start_pos)].start;
        chain.unresolved_segment = invalid;
        chain.next_start = invalid.start;
        chain.next_direction = initial_direction;
        return chain;
    }

    std::int32_t current_start_pos = start_pos;
    Point current_start = pens[static_cast<std::size_t>(start_pos)].start;
    Direction direction = initial_direction;
    std::optional<Point> candidate_floor;
    std::optional<Point> previous_confirmed_at;

    // The outer chain alone owns both clocks and candidate succession. A
    // recovery pair goes through precisely the same append transition.
    const auto append = [&](SegmentResult segment) {
        Require(segment.status == SegmentStatus::Confirmed
                    && segment.endpoint.has_value() && segment.confirmed_at.has_value(),
                "Only a complete confirmed segment can advance the chain.");
        if (previous_confirmed_at.has_value()
            && segment.confirmed_at->bar < previous_confirmed_at->bar) {
            segment.confirmed_at = previous_confirmed_at;
        }
        previous_confirmed_at = segment.confirmed_at;
        chain.confirmed_segments.push_back(std::move(segment));
        if (chain.confirmed_segments.size() >= 2U) {
            const std::size_t last = chain.confirmed_segments.size() - 1U;
            ValidateAdjacentSegments(chain.confirmed_segments[last - 1U],
                                     chain.confirmed_segments[last]);
        }
    };

    for (std::size_t iteration = 0; iteration <= pens.size(); ++iteration) {
        SegmentResult normal;
        bool short_confirmed = false;
        if (policy.enabled) {
            const auto bound = std::min(static_cast<std::int32_t>(pens.size()),
                                        current_start_pos + policy.length);
            const PenRange quick = pens.Subrange(static_cast<std::size_t>(current_start_pos),
                                                  static_cast<std::size_t>(bound));
            normal = EvaluateOneSegment(quick, 0, direction, candidate_floor, audit_sink);
            short_confirmed = normal.status == SegmentStatus::Confirmed;
        }
        if (!short_confirmed) {
            normal = EvaluateOneSegment(pens, current_start_pos, direction,
                                         candidate_floor, audit_sink);
        }
        Require(normal.direction == direction,
                "Single-segment result direction differs from requested direction.");
        Require(PointsEqual(normal.start, current_start),
                "Single-segment result start differs from the shared endpoint.");

        std::optional<std::pair<SegmentResult, SegmentResult>> recovered;
        if (policy.enabled) {
            RecoveryDiagnostic diagnostic = RecoveryDiagnostic::NormalPriority;
            if (!short_confirmed) {
                recovered = TryLongStallRecovery(pens, current_start_pos, direction,
                    candidate_floor, previous_confirmed_at, policy, diagnostic, audit_sink);
            }
            normal.recovery_diagnostic = diagnostic;
        }
        if (recovered.has_value()) {
            append(std::move(recovered->first));
            append(std::move(recovered->second));
        } else if (normal.status == SegmentStatus::Confirmed) {
            append(std::move(normal));
        } else {
            chain.unresolved_segment = std::move(normal);
            chain.next_start = current_start;
            chain.next_direction = direction;
            chain.next_segment_t_not_before = candidate_floor;
            ValidateAlternatingChain(chain.confirmed_segments);
            return chain;
        }

        const SegmentResult& last = chain.confirmed_segments.back();
        current_start = *last.endpoint;
        direction = OppositeDirection(last.direction);
        candidate_floor = last.next_segment_t_not_before;
        chain.next_start = current_start;
        chain.next_direction = direction;
        chain.next_segment_t_not_before = candidate_floor;
        const auto next = FindPenStartingAtPoint(pens, current_start, direction);
        if (!next.has_value()) {
            ValidateAlternatingChain(chain.confirmed_segments);
            return chain;
        }
        Require(*next > current_start_pos,
                "Alternating segment progression must move forward.");
        current_start_pos = *next;
    }
    InvariantFailure("Alternating segment engine exceeded its progress bound.");
}


}  // namespace

AlternatingSegmentsResult JudgeAlternatingSegments(
    const std::vector<Pen>& pens, std::int32_t start_pos, Direction direction,
    const LongStallPolicy& policy
) {
    return JudgeAlternatingSegmentsImpl(PenRange{pens}, start_pos, direction, policy);
}

AlternatingSegmentsResult JudgeAlternatingSegments(
    const DecodedSnapshot& snapshot, std::int32_t start_pos, Direction direction,
    const LongStallPolicy& policy
) {
    return JudgeAlternatingSegmentsImpl(snapshot.Range(), start_pos, direction, policy);
}

AlternatingSegmentsResult JudgeAlternatingSegmentsAudited(
    const std::vector<Pen>& pens, std::int32_t start_pos, Direction direction,
    const LongStallPolicy& policy, AuditSink& sink
) {
    return JudgeAlternatingSegmentsImpl(PenRange{pens}, start_pos, direction, policy, &sink);
}

AlternatingSegmentsResult JudgeAlternatingSegmentsAudited(
    const DecodedSnapshot& snapshot, std::int32_t start_pos, Direction direction,
    const LongStallPolicy& policy, AuditSink& sink
) {
    return JudgeAlternatingSegmentsImpl(snapshot.Range(), start_pos, direction, policy, &sink);
}


}  // namespace tdx_segment::detail
