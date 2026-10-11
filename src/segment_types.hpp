#pragma once

#include "tdx_segment/tdx_segment.hpp"
#include <cstdint>
#include <limits>
#include <optional>
#include <vector>

namespace tdx_segment::detail {

inline constexpr double kPriceEpsilon = 1.0e-9;

enum class Direction : std::int8_t {
    Down = -1,
    Up = 1,
};

enum class SegmentStatus : std::uint8_t {
    Confirmed = 0,
    Pending,
    InvalidInput,
};

enum class EndCase : std::uint8_t {
    Case1NoGap = 0,
    Case2WithGap,
    TrendA,
    LongStallRecovery,
};

struct Point {
    std::int32_t bar{-1};
    double price{};
};

struct PriceBar {
    std::int32_t bar{-1};
    double high{};
    double low{};
};

struct Pen {
    std::int32_t position{-1};
    Direction direction{Direction::Up};
    bool mirrored_prices{};
    Point start{};
    Point end{};
    // Group the small flags with position. This avoids padding around the
    // intrinsic caches on x86 while preserving ordinary aligned doubles.
    const std::vector<PriceBar>* price_store{};
    double cached_path_low{std::numeric_limits<double>::quiet_NaN()};
    double cached_path_high{std::numeric_limits<double>::quiet_NaN()};

    [[nodiscard]] double low() const noexcept;
    [[nodiscard]] double high() const noexcept;
    [[nodiscard]] bool has_price_path() const noexcept;
    [[nodiscard]] PriceBar price_bar_at(std::int32_t bar) const;
};

struct InitialSegmentStart {
    std::int32_t start_pos{-1};
    Direction direction{Direction::Up};
    Point start{};
};

struct LongStallPolicy {
    bool enabled{};
    std::int32_t length{43};
    std::int32_t stagnation{13};
    std::int32_t residence{9};
    bool last_pen_unfinished{};
};

[[nodiscard]] constexpr LongStallPolicy FormalRecoveryPolicy(
    bool last_pen_unfinished = false
) noexcept {
    return LongStallPolicy{true, 43, 13, 13, last_pen_unfinished};
}

[[nodiscard]] constexpr LongStallPolicy ExperimentalRecoveryPolicy(
    bool last_pen_unfinished = false
) noexcept {
    return LongStallPolicy{true, 43, 13, 9, last_pen_unfinished};
}

[[nodiscard]] constexpr LongStallPolicy RecoveryDisabledPolicy() noexcept {
    return LongStallPolicy{};
}

// Calibrated default. An explicit LongStallPolicy{} remains the frozen OFF path.
[[nodiscard]] constexpr LongStallPolicy DefaultLongStallPolicy(
    bool last_pen_unfinished = false
) noexcept {
    return FormalRecoveryPolicy(last_pen_unfinished);
}

enum class RecoveryWaitReason : std::uint8_t {NoCandidateCertificate, ActiveCandidate, Case1Waiting, Case2Waiting, TrendAWaiting, SuccessorFirstReverseNotReady};
struct LongStallEvidence {
    RecoveryWaitReason wait_reason{};
    std::optional<EndCase> wait_case;
    Point s{}, t{}, r{};
    std::int32_t observed_pens{}, stagnant_pens{}, residence_pens{};
    EndCase reverse_case{};
    Point reverse_intrinsic_at{}, reverse_visible_at{}, effective_at{};
};

enum class RecoveryDiagnostic : std::uint8_t {Disabled, NormalPriority, TooShort, SeedNotReady, NoStallResidence, NoLegalT, ReverseCertificateInsufficient, ActiveCandidateBlocked, Recovered};

struct SegmentResult {
    Direction direction{Direction::Up};
    SegmentStatus status{SegmentStatus::Pending};
    Point start{};
    std::optional<Point> endpoint;
    // 单段独立回看所得的证据时点；链级时钟只调整 confirmed_at。
    std::optional<Point> intrinsic_confirmed_at;
    std::optional<Point> confirmed_at;
    std::optional<EndCase> end_case;
    std::optional<Point> next_segment_t_not_before;
    std::vector<Point> explicitly_rejected_ts;
    std::optional<LongStallEvidence> recovery_evidence;
    RecoveryWaitReason wait_reason{RecoveryWaitReason::NoCandidateCertificate};
    RecoveryDiagnostic recovery_diagnostic{RecoveryDiagnostic::Disabled};
};

struct AlternatingSegmentsResult {
    std::vector<SegmentResult> confirmed_segments;
    std::optional<SegmentResult> unresolved_segment;
    Point next_start{};
    Direction next_direction{Direction::Up};
    std::optional<Point> next_segment_t_not_before;
};

struct SegmentCertificate {
    Direction direction{Direction::Up};
    std::int32_t start_bar{-1};
    std::int32_t endpoint_bar{-1};
    std::int32_t confirmed_bar{-1};
};

struct EventEncodingResult {
    bool valid{};
    std::vector<float> event_mask;
};

}  // namespace tdx_segment::detail
