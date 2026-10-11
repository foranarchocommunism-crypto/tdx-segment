#pragma once

#include "segment_engine.hpp"

namespace tdx_segment::detail {

// Private observation seam. Every identity belongs to one calculation; the
// production entrypoint creates neither a sink nor observation containers.
enum class AuditRole : std::uint8_t { Formal, NestedReaction, RecoveryPrefix };

struct AuditEvaluation {
    std::int32_t root_start{-1};
    Direction root_direction{Direction::Up};
    std::int32_t depth{};
    std::int32_t scope_start{-1};
    std::int32_t scope_last{-1};
    Direction direction{Direction::Up};
    std::int32_t candidate_floor{-1};
    AuditRole role{AuditRole::Formal};
    bool recovery_query{};
};

struct PrimaryAuditObservation {
    Point t{};
    std::int32_t first_pen{-1}, last_pen{-1};
    std::int32_t first_bar{-1}, last_bar{-1};
    double low{}, high{};
};

enum class AuditCandidateStatus : std::uint8_t {
    Confirmed, Rejected, Pending, NotApplicable,
};

struct SecondaryAuditDecision {
    std::int32_t scope_start{-1};
    Point t{};
    AuditCandidateStatus status{AuditCandidateStatus::NotApplicable};
    std::optional<Point> confirmed_at;
    std::optional<Point> successor;
    // 0: direct secondary decision; 1: inner case 2 used to confirm Trend A.
    std::uint8_t decision_role{};
};

struct Case2AuditCancellation {
    Point t{}, successor{};
    std::optional<Point> at;
};

enum class AuditStep : std::uint8_t { EvaluationStarted, NeedReaction };

class AuditSink {
public:
    virtual ~AuditSink() = default;
    virtual void OnEvaluationStep(const AuditEvaluation&, AuditStep) {}
    virtual void OnPrimaryObservation(const AuditEvaluation&, const PrimaryAuditObservation&) {}
    virtual void OnSecondaryDecision(const AuditEvaluation&, const SecondaryAuditDecision&) {}
    virtual void OnCase2Cancellation(const AuditEvaluation&, const Case2AuditCancellation&) {}
    // Result is restored to its actual price/direction, before any chain clock
    // floor. Consumers associate cancellation evidence with completed results.
    virtual void OnEvaluationCompleted(const AuditEvaluation&, const SegmentResult&) {}
};

[[nodiscard]] AlternatingSegmentsResult JudgeAlternatingSegmentsAudited(
    const std::vector<Pen>& pens,
    std::int32_t start_pos,
    Direction initial_direction,
    const LongStallPolicy& recovery_policy,
    AuditSink& sink
);

[[nodiscard]] AlternatingSegmentsResult JudgeAlternatingSegmentsAudited(
    const DecodedSnapshot& snapshot,
    std::int32_t start_pos,
    Direction initial_direction,
    const LongStallPolicy& recovery_policy,
    AuditSink& sink
);

}  // namespace tdx_segment::detail
