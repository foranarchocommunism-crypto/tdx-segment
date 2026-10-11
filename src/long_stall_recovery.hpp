#pragma once

#include "pen_snapshot.hpp"

#include <optional>
#include <utility>

namespace tdx_segment::detail {

class AuditSink;

// Recovery is called only by the outer alternating chain. Its ordinary prefix
// and reverse evidence queries use the recovery-free evaluator seam.
[[nodiscard]] std::optional<std::pair<SegmentResult, SegmentResult>> TryLongStallRecovery(
    const PenRange& pens,
    std::int32_t start_pos,
    Direction direction,
    const std::optional<Point>& candidate_floor,
    const std::optional<Point>& effective_floor,
    const LongStallPolicy& policy,
    RecoveryDiagnostic& diagnostic,
    AuditSink* audit_sink = nullptr
);

}  // namespace tdx_segment::detail
