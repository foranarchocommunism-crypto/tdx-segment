#pragma once

#include "pen_snapshot.hpp"
#include "segment_audit.hpp"

namespace tdx_segment::detail {

// Optional counters belong to one single-segment call. Production callers
// allocate no observation records. Replay counters are checked against each
// frame's monotone candidate/event cursors, rather than estimated from timing.
struct EvaluationMetrics {
    std::size_t frames_created{};
    std::size_t primary_appends{};
    std::size_t candidates_prepared{};
    std::size_t reaction_requests{};
    std::size_t reaction_resumptions{};
    std::size_t parent_candidate_replays{};
    std::size_t parent_event_replays{};
    std::size_t maximum_depth{};
};

[[nodiscard]] SegmentResult EvaluateOneSegment(
    const PenRange& pens,
    std::int32_t start_pos,
    Direction direction,
    const std::optional<Point>& candidate_t_not_before,
    AuditSink* audit_sink = nullptr,
    AuditRole audit_role = AuditRole::Formal,
    EvaluationMetrics* metrics = nullptr
);

}  // namespace tdx_segment::detail
