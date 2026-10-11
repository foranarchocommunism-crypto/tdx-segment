#include "segment_evaluator.hpp"
#include "feature_sequences.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <map>
#include <memory>
#include <stdexcept>
#include <tuple>
#include <utility>

namespace tdx_segment::detail {
namespace {
enum class CandidateStatus : std::uint8_t {
    Confirmed = 0,
    Rejected,
    Pending,
    NotApplicable,
    NeedReaction,
};

struct CandidateTop {
    Point endpoint{};
    FeatureElement a{};
    FeatureElement b{};
    FeatureElement c{};
    std::int32_t first_reverse_pen_position{-1};
    Point available_at{};
};

struct HypotheticalTurn {
    Point endpoint{};
    std::optional<FeatureElement> a;
    std::int32_t first_reverse_pen_position{-1};
};

struct TurnAttempt {
    HypotheticalTurn turn{};
    std::optional<CandidateTop> primary_top;
    std::optional<std::int32_t> pre_top_new_high_pen_position;
};

struct TrendAPattern {
    Point entered_at_point{};
    std::int32_t reaction_start_position{-1};
    double q1_end_price{};

    [[nodiscard]] Point entered_at() const noexcept {
        return entered_at_point;
    }
};

struct CandidateDecision {
    CandidateStatus status{CandidateStatus::NotApplicable};
    std::optional<Point> endpoint;
    std::optional<Point> confirmed_at;
    std::optional<EndCase> end_case;
    std::optional<Point> successor_candidate_t;
};

struct SecondaryPrefixSnapshot {
    bool completed_bottom{};
};

struct SecondaryInnovationWait {
    std::int32_t triggering_pen_position{-1};
    double fixed_start_price{};
};

// 同一次规范向上线段判断中的隔离反应前缀都来自同一条不可变 Pen 流。
// 起始 K 线、结束 K 线和笔数因此可以唯一标识一个连续反应子区间。
// 缓存只留在 Implementation 内，不改变线段模块的外部 Interface。
using ReactionPrefixKey = std::tuple<
    std::int32_t,
    std::int32_t,
    std::size_t
>;

struct ReactionRequest {
    ReactionPrefixKey key;
    PenRange pens;
    Point candidate;
    std::size_t event_index{};
};

struct EvaluationContext {
    std::map<ReactionPrefixKey, SegmentResult> reaction_prefix_results;
    AuditSink* audit_sink{};
    AuditRole root_role{AuditRole::Formal};
    AuditEvaluation audit_evaluation{};
    EvaluationMetrics* metrics{};
    std::optional<ReactionRequest> reaction_request;
};

// A child request suspends precisely this candidate and reaction event. The
// parent retains its normalized left prefix and eligibility accumulators.
struct TrendAContinuation {
    TrendAPattern pattern;
    std::size_t next_event_index{};
    std::optional<std::size_t> last_completed_event;
    bool awaiting_reaction{};
};

struct CandidateContinuation {
    TurnAttempt attempt;
    bool branch_selected{};
    std::optional<TrendAContinuation> trend;
};

void Require(bool condition, const char* message);

// 合法 Pen 流严格方向交替，同向特征笔可以通过首位置和步长 2 直接读取。
// 该 module 隐藏全局 Pen 编号与特征序列局部编号的换算，调用方无需再把
// 整段剩余历史复制成 FeatureElement/position 两个并行数组。
class AlternatingFeatureSource {
public:
    AlternatingFeatureSource(
        const PenRange& pens,
        std::int32_t first_position,
        Direction direction
    )
        : pens_(pens),
          first_position_(first_position),
          direction_(direction) {
        Require(
            first_position_ >= 0
                && first_position_ <= static_cast<std::int32_t>(pens_.size()),
            "Feature source starts outside the Pen stream."
        );
        if (first_position_ < static_cast<std::int32_t>(pens_.size())) {
            Require(
                pens_[static_cast<std::size_t>(first_position_)].direction
                    == direction_,
                "Feature source direction differs from its first Pen."
            );
        }
    }

    [[nodiscard]] std::size_t size() const noexcept {
        if (first_position_ >= static_cast<std::int32_t>(pens_.size())) {
            return 0U;
        }
        return (
            pens_.size() - 1U - static_cast<std::size_t>(first_position_)
        ) / 2U + 1U;
    }

    [[nodiscard]] std::int32_t position_at(std::size_t index) const {
        Require(index < size(), "Feature source index is out of range.");
        return first_position_ + static_cast<std::int32_t>(index * 2U);
    }

    [[nodiscard]] Pen pen_at(std::size_t index) const {
        const std::int32_t position = position_at(index);
        const Pen& pen = pens_[static_cast<std::size_t>(position)];
        Require(
            pen.position == position && pen.direction == direction_,
            "Feature source encountered a non-alternating Pen."
        );
        return pen;
    }

    [[nodiscard]] FeatureElement feature_at(std::size_t index) const {
        return PenToFeature(pen_at(index));
    }

private:
    const PenRange& pens_;
    std::int32_t first_position_;
    Direction direction_;
};

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

[[nodiscard]] Point EventNotBefore(
    const Point& event,
    const Point& floor
) noexcept {
    return event.bar >= floor.bar ? event : floor;
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

[[nodiscard]] bool IsPrimaryTopFractal(
    const FeatureElement& a,
    const FeatureElement& b,
    const FeatureElement& c
) noexcept {
    return b.high > a.high + kPriceEpsilon
        && b.high > c.high + kPriceEpsilon
        && b.low > c.low + kPriceEpsilon;
}

[[nodiscard]] bool IsSecondaryBottomFractal(
    const FeatureElement& a,
    const FeatureElement& b,
    const FeatureElement& c
) noexcept {
    return b.high < a.high - kPriceEpsilon
        && b.high < c.high - kPriceEpsilon
        && b.low < a.low - kPriceEpsilon
        && b.low < c.low - kPriceEpsilon;
}

[[nodiscard]] bool ValidateUpSegmentStart(
    const PenRange& pens,
    std::int32_t start_pos
) noexcept {
    return ThreePensDefineSegment(pens, start_pos, Direction::Up);
}

[[nodiscard]] std::optional<CandidateTop> BuildCandidateAtBoundary(
    const PenRange& pens,
    const AlternatingFeatureSource& raw_primary,
    std::int32_t b_raw_index,
    const FeatureElement& a
) {
    if (
        b_raw_index <= 0
        || b_raw_index
            >= static_cast<std::int32_t>(raw_primary.size()) - 1
    ) {
        return std::nullopt;
    }
    const Pen& first_reverse_pen = raw_primary.pen_at(
        static_cast<std::size_t>(b_raw_index)
    );
    const Point candidate_endpoint = first_reverse_pen.start;
    std::vector<FeatureElement> standardized;
    // 第二个独立标准元素一出现，本候选必然冻结或失败；无需按完整后缀
    // 预留容量。
    standardized.reserve(2U);
    AppendStandardizedFeature(
        standardized,
        raw_primary.feature_at(static_cast<std::size_t>(b_raw_index)),
        Direction::Down
    );

    for (
        std::int32_t right_end = b_raw_index + 2;
        right_end <= static_cast<std::int32_t>(raw_primary.size());
        ++right_end
    ) {
        const Pen& newest_down_pen = raw_primary.pen_at(
            static_cast<std::size_t>(right_end - 1)
        );
        if (
            newest_down_pen.start.price
            > candidate_endpoint.price + kPriceEpsilon
        ) {
            return std::nullopt;
        }

        AppendStandardizedFeature(
            standardized,
            raw_primary.feature_at(static_cast<std::size_t>(right_end - 1)),
            Direction::Down
        );
        if (standardized.size() < 2U) {
            continue;
        }

        const FeatureElement& b = standardized[0];
        const FeatureElement& c = standardized[1];
        if (!IsPrimaryTopFractal(a, b, c)) {
            return std::nullopt;
        }

        const std::int32_t last_source = std::max(
            b.source_pen_span.last_position,
            c.source_pen_span.last_position
        );
        Require(
            last_source >= 0
                && last_source < static_cast<std::int32_t>(pens.size()),
            "Candidate evidence Pen position is out of range."
        );
        const Pen& evidence_pen = pens[static_cast<std::size_t>(last_source)];
        return CandidateTop{
            candidate_endpoint,
            a,
            b,
            c,
            first_reverse_pen.position,
            evidence_pen.end,
        };
    }
    return std::nullopt;
}

[[nodiscard]] std::optional<std::int32_t>
FindFirstLaterPrimaryStartAboveT(
    const AlternatingFeatureSource& raw_primary,
    std::int32_t b_raw_index
) {
    if (
        b_raw_index < 0
        || b_raw_index
            >= static_cast<std::int32_t>(raw_primary.size())
    ) {
        return std::nullopt;
    }
    const double candidate_price = raw_primary.pen_at(
        static_cast<std::size_t>(b_raw_index)
    ).start.price;
    for (
        std::size_t index = static_cast<std::size_t>(b_raw_index + 1);
        index < raw_primary.size();
        ++index
    ) {
        const Pen& pen = raw_primary.pen_at(index);
        if (pen.start.price > candidate_price + kPriceEpsilon) {
            return pen.position;
        }
    }
    return std::nullopt;
}

[[nodiscard]] TurnAttempt BuildTurnAttemptAtBoundary(
    const PenRange& pens,
    const AlternatingFeatureSource& raw_primary,
    std::int32_t b_raw_index,
    const FeatureElement* a
) {
    const Pen& first_reverse_pen = raw_primary.pen_at(
        static_cast<std::size_t>(b_raw_index)
    );
    TurnAttempt attempt;
    attempt.turn = HypotheticalTurn{
        first_reverse_pen.start,
        a != nullptr
            ? std::optional<FeatureElement>(*a)
            : std::nullopt,
        first_reverse_pen.position,
    };
    if (a != nullptr) {
        attempt.primary_top = BuildCandidateAtBoundary(
            pens,
            raw_primary,
            b_raw_index,
            *a
        );
    }
    if (!attempt.primary_top.has_value()) {
        attempt.pre_top_new_high_pen_position =
            FindFirstLaterPrimaryStartAboveT(
                raw_primary,
                b_raw_index
            );
    }
    return attempt;
}

[[nodiscard]] std::optional<Point> FirstBarLowStrictlyBelow(
    const Pen& pen,
    double threshold
) {
    Require(
        pen.direction == Direction::Down,
        "A low-break event must be searched inside a down pen."
    );
    Require(std::isfinite(threshold), "Break threshold must be finite.");
    if (pen.has_price_path()) {
        for (
            std::int32_t bar_index = pen.start.bar;
            bar_index <= pen.end.bar;
            ++bar_index
        ) {
            const PriceBar bar = pen.price_bar_at(bar_index);
            if (bar.low < threshold - kPriceEpsilon) {
                return Point{bar.bar, bar.low};
            }
        }
    }
    if (pen.end.price < threshold - kPriceEpsilon) {
        return pen.end;
    }
    return std::nullopt;
}

[[nodiscard]] CandidateDecision JudgeCase1(
    const CandidateTop& candidate,
    const PenRange& pens
) {
    if (!IntervalOverlap(candidate.a, candidate.b)) {
        return CandidateDecision{CandidateStatus::NotApplicable};
    }

    const std::int32_t c_last_source =
        candidate.c.source_pen_span.last_position;
    Require(
        c_last_source >= 0
            && c_last_source < static_cast<std::int32_t>(pens.size()),
        "C last source position is out of range."
    );
    const Pen& c_last_source_pen =
        pens[static_cast<std::size_t>(c_last_source)];
    Require(
        c_last_source_pen.position == c_last_source,
        "C last source position must index the validated Pen stream."
    );
    CandidateDecision decision;
    decision.endpoint = candidate.endpoint;
    std::int32_t equal_extreme_count = 0;
    double latest_equal_source_low = candidate.b.low;
    // 冻结 B 内同价极值取最后物理端点。重复同价时，CZSC 还要求
    // 后续向下笔严格越过最后同价来源笔的原始低边界。
    for (std::int32_t pos = candidate.b.source_pen_span.first_position;
         pos <= candidate.b.source_pen_span.last_position; pos += 2) {
        const Pen& source_pen = pens[static_cast<std::size_t>(pos)];
        if (AlmostEqual(source_pen.start.price, candidate.endpoint.price)) {
            ++equal_extreme_count;
            latest_equal_source_low = source_pen.low();
            if (source_pen.start.bar > decision.endpoint->bar) {
                decision.endpoint = source_pen.start;
            }
        }
    }
    Require(
        equal_extreme_count >= 1,
        "Frozen B must contain the candidate endpoint source."
    );
    const double threshold = equal_extreme_count > 1
        ? std::min(candidate.b.low, latest_equal_source_low)
        : candidate.b.low;

    auto confirmed_at = FirstBarLowStrictlyBelow(
        c_last_source_pen,
        threshold
    );
    if (!confirmed_at.has_value()) {
        // 同价候选在冻结 C 尚未越界时保持待定。后来同方向特征笔可以
        // 确认同一 T；若其起点先创新高，则旧 T 被取消并接续该新点。
        for (std::int32_t pos = c_last_source + 2;
             pos < static_cast<std::int32_t>(pens.size()); pos += 2) {
            const Pen& later_down_pen = pens[static_cast<std::size_t>(pos)];
            Require(
                later_down_pen.direction == Direction::Down,
                "A later case-1 confirmation source must be a down pen."
            );
            if (
                later_down_pen.start.price
                > candidate.endpoint.price + kPriceEpsilon
            ) {
                CandidateDecision rejected;
                rejected.status = CandidateStatus::Rejected;
                rejected.successor_candidate_t = later_down_pen.start;
                return rejected;
            }
            confirmed_at = FirstBarLowStrictlyBelow(
                later_down_pen,
                threshold
            );
            if (confirmed_at.has_value()) {
                break;
            }
        }
    }
    if (!confirmed_at.has_value()) {
        return CandidateDecision{CandidateStatus::Pending};
    }
    decision.status = CandidateStatus::Confirmed;
    decision.confirmed_at = *confirmed_at;
    decision.end_case = EndCase::Case1NoGap;
    return decision;
}

[[nodiscard]] std::optional<std::size_t> SelectSecondaryABeforeB(
    const SecondaryHighestHighTracker& highest_high_tracker,
    const std::vector<FeatureElement>& standardized,
    std::size_t b_index
) {
    Require(
        b_index <= standardized.size(),
        "A secondary B2 index is out of range."
    );
    return highest_high_tracker.SelectBeforeB(b_index);
}

[[nodiscard]] bool
FindNewBottomCreatedByLatestElement(
    const std::vector<FeatureElement>& standardized,
    std::int32_t latest_raw_pen_position,
    const std::optional<std::size_t>& a_index
) {
    if (standardized.size() < 3U) {
        return false;
    }
    const std::size_t last = standardized.size() - 1U;
    const FeatureElement& b = standardized[last - 1U];
    const FeatureElement& c = standardized[last];
    Require(
        c.source_pen_span.contains(latest_raw_pen_position),
        "The latest source pen must belong to the last standardized element."
    );
    if (
        c.source_pen_span.first_position != latest_raw_pen_position
        || c.source_pen_span.last_position != latest_raw_pen_position
    ) {
        // 低低包含只能降低或保持尾元素的 high/low。当前原始笔若参与
        // 包含，就不可能在一个尚无底分型的活动前缀中首次制造右侧
        // 同时抬高的独立 C2。
        return false;
    }

    Require(
        a_index.has_value() && *a_index < last - 1U,
        "A secondary B2 must have at least one standardized element before it."
    );
    return IsSecondaryBottomFractal(standardized[*a_index], b, c);
}

[[nodiscard]] SecondaryPrefixSnapshot AnalyzeSecondaryPrefix(
    const std::vector<FeatureElement>& standardized,
    const Pen& latest_pen,
    const std::optional<std::size_t>& bottom_a_index
) {
    Require(
        !standardized.empty(),
        "Standardized secondary prefix may not be empty."
    );
    SecondaryPrefixSnapshot snapshot;
    const FeatureElement& current = standardized.back();
    Require(
        current.source_pen_span.contains(latest_pen.position),
        "The latest source pen must belong to the current standardized element."
    );
    snapshot.completed_bottom = FindNewBottomCreatedByLatestElement(
        standardized,
        latest_pen.position,
        bottom_a_index
    );
    return snapshot;
}

[[nodiscard]] CandidateDecision ConfirmFromSecondaryBottom(
    const CandidateTop& candidate,
    const SecondaryPrefixSnapshot& snapshot,
    const Pen& latest_pen
) {
    Require(
        snapshot.completed_bottom,
        "A secondary bottom certificate is required."
    );
    CandidateDecision decision;
    decision.status = CandidateStatus::Confirmed;
    decision.endpoint = candidate.endpoint;
    decision.confirmed_at = EventNotBefore(
        latest_pen.end,
        candidate.available_at
    );
    decision.end_case = EndCase::Case2WithGap;
    return decision;
}

[[nodiscard]] CandidateDecision RejectFromSecondaryRawInnovation(
    const CandidateTop& candidate,
    const Pen& triggering_pen,
    const Pen& decided_at_pen
) {
    Require(
        triggering_pen.direction == Direction::Up,
        "A secondary innovation trigger must be an up pen."
    );
    Require(
        triggering_pen.end.price
            > candidate.endpoint.price + kPriceEpsilon,
        "The raw secondary innovation endpoint must exceed T."
    );
    Require(
        decided_at_pen.end.bar >= triggering_pen.end.bar,
        "An innovation exit may not precede its triggering pen."
    );
    CandidateDecision decision;
    decision.status = CandidateStatus::Rejected;
    decision.endpoint = candidate.endpoint;
    decision.confirmed_at = decided_at_pen.end;
    decision.end_case = EndCase::Case2WithGap;
    decision.successor_candidate_t = triggering_pen.end;
    return decision;
}

struct SecondaryTailAnchor {
    FeatureElement a2{};
    FeatureElement b2{};
};

// 第二特征序列前缀把 Append -> 最高索引同步 -> 分型分析的隐藏时序收进
// 一个 module。调用方只提交最新原始笔，并查询本次前缀结论或尾部 A2/B2。
class SecondaryFeaturePrefix {
public:
    [[nodiscard]] SecondaryPrefixSnapshot Append(const Pen& latest_pen) {
        const std::size_t changed_from = AppendStandardizedFeature(
            standardized_,
            PenToFeature(latest_pen),
            Direction::Up
        );
        highest_high_tracker_.SyncFrom(standardized_, changed_from);
        const auto bottom_a_index = standardized_.size() >= 3U
            ? SelectSecondaryABeforeB(
                highest_high_tracker_,
                standardized_,
                standardized_.size() - 2U
            )
            : std::nullopt;
        return AnalyzeSecondaryPrefix(
            standardized_,
            latest_pen,
            bottom_a_index
        );
    }

    [[nodiscard]] bool LatestContains(
        std::int32_t raw_pen_position
    ) const {
        Require(
            !standardized_.empty(),
            "A secondary source query requires a nonempty prefix."
        );
        return standardized_.back().source_pen_span.contains(raw_pen_position);
    }

    [[nodiscard]] std::optional<SecondaryTailAnchor>
    SelectABeforeTail() const {
        Require(
            !standardized_.empty(),
            "A secondary anchor query requires a nonempty prefix."
        );
        const std::size_t b_index = standardized_.size() - 1U;
        const auto a_index = SelectSecondaryABeforeB(
            highest_high_tracker_,
            standardized_,
            b_index
        );
        if (!a_index.has_value()) {
            return std::nullopt;
        }
        return SecondaryTailAnchor{
            standardized_[*a_index],
            standardized_[b_index],
        };
    }

private:
    std::vector<FeatureElement> standardized_;
    SecondaryHighestHighTracker highest_high_tracker_;
};

[[nodiscard]] CandidateDecision JudgeSecondarySequence(
    const CandidateTop& candidate,
    const PenRange& pens
) {
    const AlternatingFeatureSource raw_secondary(
        pens,
        candidate.first_reverse_pen_position + 1,
        Direction::Up
    );
    SecondaryFeaturePrefix secondary_prefix;
    std::optional<SecondaryInnovationWait> innovation_wait;

    for (std::size_t prefix_length = 1; prefix_length <= raw_secondary.size();
         ++prefix_length) {
        const Pen& latest_pen = raw_secondary.pen_at(prefix_length - 1U);

        if (innovation_wait.has_value()) {
            Require(
                latest_pen.position
                    != innovation_wait->triggering_pen_position,
                "A raw innovation trigger may enter its wait state only once."
            );
            if (
                latest_pen.start.price
                    < innovation_wait->fixed_start_price - kPriceEpsilon
            ) {
                const Pen& triggering_pen = pens[static_cast<std::size_t>(
                    innovation_wait->triggering_pen_position
                )];
                return RejectFromSecondaryRawInnovation(
                    candidate,
                    triggering_pen,
                    latest_pen
                );
            }
        }

        const bool raw_endpoint_above_t =
            latest_pen.end.price
                > candidate.endpoint.price + kPriceEpsilon;
        const SecondaryPrefixSnapshot snapshot = secondary_prefix.Append(
            latest_pen
        );

        if (snapshot.completed_bottom) {
            return ConfirmFromSecondaryBottom(
                candidate,
                snapshot,
                latest_pen
            );
        }

        if (innovation_wait.has_value()) {
            if (
                secondary_prefix.LatestContains(
                    innovation_wait->triggering_pen_position
                )
            ) {
                // 当前后来笔仍被 B* 吸收；相等起点也落入这里。固定
                // U.start 不变，继续等待真正独立的 C2。
                continue;
            }
            InvariantFailure(
                "A qualified secondary innovation produced an independent C2 "
                "without completing its bottom fractal."
            );
        }

        // 固定 U.start 只属于原始创新高分支。普通未创新高前缀即使
        // 起点下降，也只能继续包含和底分型流程。
        if (!raw_endpoint_above_t) {
            continue;
        }

        const auto tail_anchor = secondary_prefix.SelectABeforeTail();
        if (!tail_anchor.has_value()) {
            return RejectFromSecondaryRawInnovation(
                candidate,
                latest_pen,
                latest_pen
            );
        }

        const FeatureElement& a2 = tail_anchor->a2;
        const FeatureElement& b2 = tail_anchor->b2;
        if (!(a2.high > b2.high + kPriceEpsilon)) {
            return RejectFromSecondaryRawInnovation(
                candidate,
                latest_pen,
                latest_pen
            );
        }
        Require(
            a2.low > b2.low + kPriceEpsilon,
            "Before the first raw innovation, a highest A2 above B* must also "
            "have a strictly higher low."
        );
        innovation_wait = SecondaryInnovationWait{
            latest_pen.position,
            latest_pen.start.price,
        };
    }

    CandidateDecision pending;
    pending.status = CandidateStatus::Pending;
    pending.endpoint = candidate.endpoint;
    return pending;
}

[[nodiscard]] const FeatureElement* CandidateLeftA(
    const CandidateTop& candidate
) noexcept {
    return &candidate.a;
}

[[nodiscard]] const FeatureElement* CandidateLeftA(
    const HypotheticalTurn& candidate
) noexcept {
    return candidate.a.has_value() ? &*candidate.a : nullptr;
}

template <typename Candidate>
[[nodiscard]] std::optional<TrendAPattern> FindTrendAPattern(
    const Candidate& candidate,
    const PenRange& pens,
    std::int32_t segment_start_position,
    bool first_pen_already_exceeded,
    bool three_pen_progress_seen
) {
    const FeatureElement* left_a = CandidateLeftA(candidate);
    if (left_a == nullptr) {
        return std::nullopt;
    }
    const std::int32_t q1_position = candidate.first_reverse_pen_position;
    if (
        q1_position < 0
        || q1_position + 3 >= static_cast<std::int32_t>(pens.size())
    ) {
        return std::nullopt;
    }
    const std::int32_t pre_t_pen_count = q1_position - segment_start_position;
    if (segment_start_position < 0 || pre_t_pen_count < 3 || pre_t_pen_count % 2 != 1) {
        return std::nullopt;
    }
    if (!PointsEqual(
        pens[static_cast<std::size_t>(q1_position - 1)].end, candidate.endpoint
    )) {
        return std::nullopt;
    }
    // Only a three-pen prefix reuses strict initialization. Longer prefixes
    // accept a first-pen breakthrough OR any qualifying local triple through T.
    if (pre_t_pen_count == 3) {
        if (!ThreePensMeetStrictInitialProgress(pens, segment_start_position, Direction::Up)) {
            return std::nullopt;
        }
    } else if (!first_pen_already_exceeded && !three_pen_progress_seen) {
        return std::nullopt;
    }
    const Pen& q1 = pens[static_cast<std::size_t>(q1_position)];
    const Pen& seed_0 = pens[static_cast<std::size_t>(q1_position + 1)];
    const Pen& seed_1 = pens[static_cast<std::size_t>(q1_position + 2)];
    const Pen& seed_2 = pens[static_cast<std::size_t>(q1_position + 3)];
    if (q1.position != q1_position) {
        return std::nullopt;
    }
    if (
        seed_0.position != q1_position + 1
        || seed_1.position != q1_position + 2
        || seed_2.position != q1_position + 3
    ) {
        return std::nullopt;
    }
    if (q1.direction != Direction::Down) {
        return std::nullopt;
    }
    // left_a 与普通分支共用 SPEC-034 的最近 / 特殊最高标准元素。走势 A 必须
    // 严格跌破这个候选相关的 A；没有合格 A 时已在上方直接返回。
    if (q1.low() >= left_a->high - kPriceEpsilon) {
        return std::nullopt;
    }
    if (
        seed_0.direction != Direction::Up
        || seed_1.direction != Direction::Down
        || seed_2.direction != Direction::Up
    ) {
        return std::nullopt;
    }
    const double overlap_low = std::max({
        seed_0.low(),
        seed_1.low(),
        seed_2.low(),
    });
    const double overlap_high = std::min({
        seed_0.high(),
        seed_1.high(),
        seed_2.high(),
    });
    if (overlap_low > overlap_high + kPriceEpsilon) {
        return std::nullopt;
    }
    if (seed_2.high() <= seed_0.high() + kPriceEpsilon) {
        return std::nullopt;
    }
    const double seed_high = std::max({
        seed_0.high(),
        seed_1.high(),
        seed_2.high(),
    });
    if (seed_high > candidate.endpoint.price + kPriceEpsilon) {
        return std::nullopt;
    }
    const double seed_low = std::min({
        seed_0.low(),
        seed_1.low(),
        seed_2.low(),
    });
    if (seed_low < q1.low() - kPriceEpsilon) {
        return std::nullopt;
    }

    return TrendAPattern{
        seed_2.end,
        q1_position + 1,
        q1.end.price,
    };
}

[[nodiscard]] std::optional<EndCase>
ConfirmingCaseOfIsolatedReactionPrefix(
    const PenRange& reaction_prefix,
    const Point& candidate,
    std::size_t event_index,
    EvaluationContext& context
) {
    if (reaction_prefix.size() < 3U) {
        return std::nullopt;
    }

    const ReactionPrefixKey key{
        reaction_prefix.front().start.bar,
        reaction_prefix.back().end.bar,
        reaction_prefix.size(),
    };
    auto cached = context.reaction_prefix_results.find(key);
    if (cached == context.reaction_prefix_results.end()) {
        if (context.audit_sink != nullptr) {
            context.audit_sink->OnEvaluationStep(context.audit_evaluation, AuditStep::NeedReaction);
        }
        Require(!context.reaction_request.has_value(), "One step may request only one reaction prefix.");
        context.reaction_request = ReactionRequest{key, reaction_prefix, candidate, event_index};
        if (context.metrics != nullptr) { ++context.metrics->reaction_requests; }
        return std::nullopt;
    }

    const SegmentResult& result = cached->second;
    if (result.status == SegmentStatus::Confirmed) {
        Require(
            result.end_case.has_value(),
            "A confirmed inner segment must identify its end case."
        );
        return result.end_case;
    }
    return std::nullopt;
}

template <typename Candidate>
[[nodiscard]] CandidateDecision ConfirmOuterFromInnerSegment(
    const Candidate& candidate,
    const Pen& trigger_pen
) {
    CandidateDecision decision;
    decision.status = CandidateStatus::Confirmed;
    decision.endpoint = candidate.endpoint;
    decision.confirmed_at = trigger_pen.end;
    decision.end_case = EndCase::TrendA;
    return decision;
}

template <typename Candidate>
[[nodiscard]] CandidateDecision JudgeTrendASpecial(
    const Candidate& candidate,
    TrendAContinuation& continuation,
    const PenRange& pens,
    EvaluationContext& context
) {
    const TrendAPattern& pattern = continuation.pattern;
    const std::size_t reaction_start = static_cast<std::size_t>(
        pattern.reaction_start_position
    );
    for (std::size_t index = continuation.next_event_index; index < pens.size(); ++index) {
        if (continuation.awaiting_reaction) {
            // The child's result becomes visible at exactly the suspended
            // event. None of the preceding events is visited a second time.
            continuation.awaiting_reaction = false;
            if (context.metrics != nullptr) { ++context.metrics->reaction_resumptions; }
        } else {
            const bool replay = continuation.last_completed_event.has_value()
                && index <= *continuation.last_completed_event;
            if (replay && context.metrics != nullptr) { ++context.metrics->parent_event_replays; }
            Require(!replay, "A completed Trend-A event may not be replayed.");
        }
        const PenRange observed = pens.Subrange(reaction_start, index + 1U);
        const Pen& pen = pens[index];

        if (
            pen.direction == Direction::Up
            && pen.end.price > candidate.endpoint.price + kPriceEpsilon
        ) {
            const auto same_event_case =
                ConfirmingCaseOfIsolatedReactionPrefix(observed, candidate.endpoint, index, context);
            if (context.reaction_request.has_value()) {
                continuation.next_event_index = index;
                continuation.awaiting_reaction = true;
                CandidateDecision need;
                need.status = CandidateStatus::NeedReaction;
                return need;
            }
            if (
                same_event_case.has_value()
                && *same_event_case == EndCase::Case2WithGap
            ) {
                if (context.audit_sink != nullptr) {
                    context.audit_sink->OnSecondaryDecision(context.audit_evaluation, SecondaryAuditDecision{
                        context.audit_evaluation.root_start, candidate.endpoint,
                        AuditCandidateStatus::Confirmed, pen.end, std::nullopt, 1,
                    });
                }
                return ConfirmOuterFromInnerSegment(candidate, pen);
            }

            CandidateDecision rejected;
            rejected.status = CandidateStatus::Rejected;
            rejected.endpoint = candidate.endpoint;
            rejected.confirmed_at = pen.end;
            rejected.end_case = EndCase::TrendA;
            rejected.successor_candidate_t = pen.end;
            return rejected;
        }

        if (
            pen.direction == Direction::Down
            && pen.end.price < pattern.q1_end_price - kPriceEpsilon
        ) {
            return ConfirmOuterFromInnerSegment(candidate, pen);
        }

        const auto inner_case =
            ConfirmingCaseOfIsolatedReactionPrefix(observed, candidate.endpoint, index, context);
        if (context.reaction_request.has_value()) {
            continuation.next_event_index = index;
            continuation.awaiting_reaction = true;
            CandidateDecision need;
            need.status = CandidateStatus::NeedReaction;
            return need;
        }
        if (inner_case.has_value()) {
            if (*inner_case == EndCase::Case2WithGap && context.audit_sink != nullptr) {
                context.audit_sink->OnSecondaryDecision(context.audit_evaluation, SecondaryAuditDecision{
                    context.audit_evaluation.root_start, candidate.endpoint,
                    AuditCandidateStatus::Confirmed, pen.end, std::nullopt, 1,
                });
            }
            return ConfirmOuterFromInnerSegment(candidate, pen);
        }
        continuation.last_completed_event = index;
        continuation.next_event_index = index + 1U;
    }

    CandidateDecision pending;
    pending.status = CandidateStatus::Pending;
    pending.endpoint = candidate.endpoint;
    pending.end_case = EndCase::TrendA;
    return pending;
}

[[nodiscard]] CandidateDecision ResolveWithoutPrimaryTop(
    const HypotheticalTurn& turn,
    const PenRange& pens,
    const std::optional<std::int32_t>& pre_top_new_high_pen_position,
    std::int32_t segment_start_position,
    bool first_pen_already_exceeded,
    bool three_pen_progress_seen,
    CandidateContinuation& continuation,
    EvaluationContext& context
) {
    if (continuation.trend.has_value()) {
        return JudgeTrendASpecial(turn, *continuation.trend, pens, context);
    }
    std::optional<Pen> pre_top_new_high_value;
    const Pen* pre_top_new_high_pen = nullptr;
    if (pre_top_new_high_pen_position.has_value()) {
        Require(
            *pre_top_new_high_pen_position >= 0
                && *pre_top_new_high_pen_position
                    < static_cast<std::int32_t>(pens.size()),
            "A pre-top new-high pen position is out of range."
        );
        pre_top_new_high_value = pens[static_cast<std::size_t>(*pre_top_new_high_pen_position)];
        pre_top_new_high_pen = &*pre_top_new_high_value;
    }
    if (!continuation.branch_selected) {
        const auto trend_a = FindTrendAPattern(turn, pens, segment_start_position,
            first_pen_already_exceeded, three_pen_progress_seen);
        if (trend_a.has_value() && (pre_top_new_high_pen == nullptr
                || trend_a->entered_at().bar <= pre_top_new_high_pen->start.bar)) {
            continuation.trend = TrendAContinuation{*trend_a,
                static_cast<std::size_t>(trend_a->reaction_start_position), std::nullopt, false};
        }
        continuation.branch_selected = true;
    }
    if (continuation.trend.has_value()) {
        return JudgeTrendASpecial(turn, *continuation.trend, pens, context);
    }

    if (pre_top_new_high_pen == nullptr) {
        CandidateDecision not_applicable;
        not_applicable.status = CandidateStatus::NotApplicable;
        not_applicable.endpoint = turn.endpoint;
        return not_applicable;
    }

    Require(
        pre_top_new_high_pen->position > 0,
        "A pre-top new-high pen must have a preceding up pen."
    );
    const Pen& triggering_up = pens[
        static_cast<std::size_t>(pre_top_new_high_pen->position - 1)
    ];
    Require(
        triggering_up.direction == Direction::Up,
        "A pre-top high start must be a real up-pen endpoint."
    );
    Require(
        PointsEqual(triggering_up.end, pre_top_new_high_pen->start),
        "The pre-top high start must equal the preceding up-pen endpoint."
    );

    CandidateDecision rejected;
    rejected.status = CandidateStatus::Rejected;
    rejected.endpoint = turn.endpoint;
    rejected.confirmed_at = pre_top_new_high_pen->start;
    rejected.successor_candidate_t = triggering_up.end;
    return rejected;
}

[[nodiscard]] CandidateDecision JudgeOneCandidate(
    const CandidateTop& candidate,
    const PenRange& pens,
    std::int32_t segment_start_position,
    bool first_pen_already_exceeded,
    bool three_pen_progress_seen,
    CandidateContinuation& continuation,
    EvaluationContext& context
) {
    if (!continuation.branch_selected) {
        const auto trend_a = FindTrendAPattern(candidate, pens, segment_start_position,
            first_pen_already_exceeded, three_pen_progress_seen);
        if (trend_a.has_value()) {
            Require(trend_a->entered_at().bar < candidate.available_at.bar,
                "A valid Trend-A seed must enter before A-B-C is available.");
            continuation.trend = TrendAContinuation{*trend_a,
                static_cast<std::size_t>(trend_a->reaction_start_position), std::nullopt, false};
        }
        continuation.branch_selected = true;
    }
    if (continuation.trend.has_value()) {
        return JudgeTrendASpecial(candidate, *continuation.trend, pens, context);
    }

    const CandidateDecision case_1 = JudgeCase1(candidate, pens);
    if (case_1.status != CandidateStatus::NotApplicable) {
        return case_1;
    }
    Require(
        IntervalGap(candidate.a, candidate.b),
        "A/B must either overlap or have a strict feature-sequence gap."
    );
    const auto decision = JudgeSecondarySequence(candidate, pens);
    if (context.audit_sink != nullptr) {
        context.audit_sink->OnSecondaryDecision(context.audit_evaluation, SecondaryAuditDecision{
            pens[static_cast<std::size_t>(segment_start_position)].start.bar,
            candidate.endpoint, static_cast<AuditCandidateStatus>(decision.status),
            decision.confirmed_at, decision.successor_candidate_t, 0,
        });
    }
    return decision;
}

[[nodiscard]] std::int32_t FindLastPenEndingAtOrBefore(
    const PenRange& pens, std::int32_t bar
) {
    std::size_t begin = 0U, end = pens.size();
    while (begin < end) {
        const std::size_t middle = begin + (end - begin) / 2U;
        if (pens[middle].end.bar <= bar) { begin = middle + 1U; }
        else { end = middle; }
    }
    Require(begin > 0U, "A confirmed endpoint must follow at least one completed pen.");
    return static_cast<std::int32_t>(begin - 1U);
}

[[nodiscard]] SegmentResult BuildFinalSegmentResult(
    const PenRange& pens,
    std::int32_t start_pos,
    const CandidateDecision& decision
) {
    Require(decision.endpoint.has_value(), "Confirmed decision lacks endpoint.");
    Require(
        decision.confirmed_at.has_value(),
        "Confirmed decision lacks confirmation event."
    );
    Require(decision.end_case.has_value(), "Confirmed decision lacks end case.");

    const std::int32_t endpoint_pos = FindLastPenEndingAtOrBefore(
        pens,
        decision.endpoint->bar
    );
    const std::int32_t included_count = endpoint_pos - start_pos + 1;
    Require(included_count >= 3, "A segment must contain at least three pens.");
    Require(
        included_count % 2 == 1,
        "A canonical up segment must contain an odd number of pens."
    );
    Require(
        HasStrictSegmentPriceDirection(
            Direction::Up,
            pens[static_cast<std::size_t>(start_pos)].start,
            *decision.endpoint
        ),
        "A confirmed up-segment endpoint must be strictly above its start."
    );

    SegmentResult result;
    result.direction = Direction::Up;
    result.status = SegmentStatus::Confirmed;
    result.start = pens[static_cast<std::size_t>(start_pos)].start;
    result.endpoint = decision.endpoint;
    result.intrinsic_confirmed_at = decision.confirmed_at;
    result.confirmed_at = decision.confirmed_at;
    result.end_case = decision.end_case;
    if (*decision.end_case == EndCase::TrendA) {
        result.next_segment_t_not_before = decision.confirmed_at;
    }
    return result;
}

struct EvaluationFrame {
    PenRange pens;
    std::int32_t start_pos{};
    std::optional<Point> candidate_t_not_before;
    std::optional<ReactionPrefixKey> cache_key;
    AuditEvaluation audit_evaluation;
    bool audit_started{};
    bool initialized{};
    std::optional<AlternatingFeatureSource> raw_primary;
    std::optional<PrimaryLeftPrefix> primary_left;
    std::vector<Point> explicitly_rejected_ts;
    std::vector<Point> unresolved_successor_ts;
    std::optional<std::int32_t> previous_turn_bar;
    bool first_pen_already_exceeded{};
    bool three_pen_progress_seen{};
    std::int32_t b_raw_index{1};
    std::int32_t last_prepared_candidate{};
    std::optional<CandidateContinuation> current_candidate;
    std::optional<ReactionRequest> suspended_request;

    EvaluationFrame(const PenRange& scope, std::int32_t start,
        const std::optional<Point>& floor,
        const std::optional<ReactionPrefixKey>& key)
        : pens(scope), start_pos(start), candidate_t_not_before(floor), cache_key(key) {}

    void AdvanceCandidate() {
        current_candidate.reset();
        ++b_raw_index;
    }
};

enum class FrameStepStatus : std::uint8_t { Completed, NeedReaction };

struct FrameStep {
    FrameStepStatus status{FrameStepStatus::Completed};
    std::optional<SegmentResult> result;

    FrameStep(SegmentResult completed) : result(std::move(completed)) {}
    explicit FrameStep(FrameStepStatus requested) : status(requested) {}
};

// One step either finishes the frame or yields its current reaction request.
// Every normalization/candidate preparation lives on the frame and runs once.
[[nodiscard]] FrameStep StepCanonicalUpSegment(
    EvaluationFrame& frame,
    EvaluationContext& context
) {
    const PenRange& pens = frame.pens;
    const std::int32_t start_pos = frame.start_pos;
    const auto& candidate_t_not_before = frame.candidate_t_not_before;
    if (!frame.initialized) {
        Require(!pens.empty(), "Canonical segment engine requires a nonempty pen stream.");
        Require(start_pos >= 0 && start_pos < static_cast<std::int32_t>(pens.size()),
            "Canonical segment start position is out of range.");
        if (!ValidateUpSegmentStart(pens, start_pos)) {
            SegmentResult pending;
            pending.direction = Direction::Up;
            pending.status = SegmentStatus::Pending;
            pending.start = pens[static_cast<std::size_t>(start_pos)].start;
            return pending;
        }
        frame.raw_primary.emplace(pens, start_pos + 1, Direction::Down);
        frame.primary_left.emplace();
        frame.initialized = true;
    }
    const AlternatingFeatureSource& raw_primary = *frame.raw_primary;
    PrimaryLeftPrefix& primary_left = *frame.primary_left;
    auto& explicitly_rejected_ts = frame.explicitly_rejected_ts;
    auto& unresolved_successor_ts = frame.unresolved_successor_ts;
    auto& previous_turn_bar = frame.previous_turn_bar;
    auto& first_pen_already_exceeded = frame.first_pen_already_exceeded;
    auto& three_pen_progress_seen = frame.three_pen_progress_seen;
    // 候选必须按时间逐个作出结论；一旦确认或待定，任何更晚候选都
    // 不再可见。因此这里只增量维护左侧标准序列，并在轮到当前边界时
    // 按 SPEC-034 查询普通最近 / 特殊最高 A，再构造右侧证据。尚未抵达的 T-prime
    // 仍由下方顺序断言保护。
    while (frame.b_raw_index < static_cast<std::int32_t>(raw_primary.size())) {
        const std::int32_t b_raw_index = frame.b_raw_index;
        if (!frame.current_candidate.has_value()) {
            const bool replay = b_raw_index <= frame.last_prepared_candidate;
            if (replay && context.metrics != nullptr) { ++context.metrics->parent_candidate_replays; }
            Require(!replay, "A prepared parent candidate may not be replayed.");
            frame.last_prepared_candidate = b_raw_index;
            if (context.metrics != nullptr) {
                ++context.metrics->primary_appends;
                ++context.metrics->candidates_prepared;
            }
            primary_left.Append(
                raw_primary.feature_at(static_cast<std::size_t>(b_raw_index - 1))
            );
            const Pen& first_reverse_pen = raw_primary.pen_at(
                static_cast<std::size_t>(b_raw_index)
            );
            const FeatureElement* a = primary_left.CurrentA(
                first_reverse_pen.start.price,
                first_reverse_pen
            );
            const HypotheticalTurn turn{
                first_reverse_pen.start,
                a != nullptr
                    ? std::optional<FeatureElement>(*a)
                    : std::nullopt,
                first_reverse_pen.position,
            };
            // Include this T's preceding up pen, before any floor/filter skips.
            first_pen_already_exceeded = first_pen_already_exceeded
                || first_reverse_pen.start.price
                    > pens[static_cast<std::size_t>(start_pos)].high() + kPriceEpsilon;
            const std::int32_t local_start = first_reverse_pen.position - 3;
            if (local_start >= start_pos && ThreePensDefineSegment(pens, local_start, Direction::Up)) {
                const Pen& first = pens[static_cast<std::size_t>(local_start)];
                const Pen& second = pens[static_cast<std::size_t>(local_start + 1)];
                const Pen& third = pens[static_cast<std::size_t>(local_start + 2)];
                three_pen_progress_seen = three_pen_progress_seen
                    || (second.end.price >= first.start.price - kPriceEpsilon
                        && third.end.price > first.end.price + kPriceEpsilon);
            }
            if (previous_turn_bar.has_value()) {
                Require(
                    *previous_turn_bar < turn.endpoint.bar,
                    "Candidate attempts must be generated in strict time order."
                );
            }
            previous_turn_bar = turn.endpoint.bar;

            if (
                candidate_t_not_before.has_value()
                && turn.endpoint.bar < candidate_t_not_before->bar
            ) {
                ++frame.b_raw_index;
                continue;
            }

            if (!unresolved_successor_ts.empty()) {
                for (const Point& successor : unresolved_successor_ts) {
                    Require(
                        successor.bar >= turn.endpoint.bar,
                        "Candidate enumeration may not pass an unchecked T-prime."
                    );
                }
                const bool reached_successor = std::any_of(
                    unresolved_successor_ts.begin(),
                    unresolved_successor_ts.end(),
                    [&turn](const Point& successor) {
                        return PointsEqual(turn.endpoint, successor);
                    }
                );
                if (reached_successor) {
                    unresolved_successor_ts.erase(
                        std::remove_if(
                            unresolved_successor_ts.begin(),
                            unresolved_successor_ts.end(),
                            [&turn](const Point& successor) {
                                return PointsEqual(turn.endpoint, successor);
                            }
                        ),
                        unresolved_successor_ts.end()
                    );
                } else {
                    const auto next_successor = std::min_element(
                        unresolved_successor_ts.begin(),
                        unresolved_successor_ts.end(),
                        [](const Point& left, const Point& right) {
                            return left.bar < right.bar;
                        }
                    );
                    Require(
                        next_successor != unresolved_successor_ts.end()
                            && turn.endpoint.bar < next_successor->bar,
                        "A same-bar T-prime must equal the real candidate point."
                    );
                }
            }

            // 线段方向是结构语义，不只是交替枚举。一个不严格高于当前
            // 规范向上线段起点的 T 从一开始就没有端点候选资格：它不能
            // 进入普通分支、走势 A 或候选接续，也不能阻塞更晚合法候选。
            if (!HasStrictSegmentPriceDirection(
                Direction::Up,
                pens[static_cast<std::size_t>(start_pos)].start,
                turn.endpoint
            )) {
                ++frame.b_raw_index;
                continue;
            }

            if (context.audit_sink != nullptr && a == nullptr) {
                const FeatureElement* nearest = primary_left.NearestStandard();
                if (nearest != nullptr && nearest->high > turn.endpoint.price + kPriceEpsilon) {
                    context.audit_sink->OnPrimaryObservation(context.audit_evaluation, PrimaryAuditObservation{
                        turn.endpoint, nearest->source_pen_span.first_position,
                        nearest->source_pen_span.last_position, nearest->first_bar,
                        nearest->last_bar, nearest->low, nearest->high,
                    });
                }
            }
            TurnAttempt attempt = BuildTurnAttemptAtBoundary(
                pens,
                raw_primary,
                b_raw_index,
                a
            );

            frame.current_candidate = CandidateContinuation{std::move(attempt), false, std::nullopt};
        }
        CandidateContinuation& continuation = *frame.current_candidate;
        const TurnAttempt& attempt = continuation.attempt;
        const HypotheticalTurn& turn = attempt.turn;

        CandidateDecision decision;
        if (attempt.primary_top.has_value()) {
            decision = JudgeOneCandidate(
                *attempt.primary_top,
                pens,
                start_pos,
                first_pen_already_exceeded,
                three_pen_progress_seen,
                continuation,
                context
            );
        } else {
            decision = ResolveWithoutPrimaryTop(
                turn,
                pens,
                attempt.pre_top_new_high_pen_position,
                start_pos,
                first_pen_already_exceeded,
                three_pen_progress_seen,
                continuation,
                context
            );
        }

        if (decision.status == CandidateStatus::NeedReaction) {
            Require(context.reaction_request.has_value(), "A suspended candidate lacks its child request.");
            return FrameStep(FrameStepStatus::NeedReaction);
        }
        if (decision.status == CandidateStatus::NotApplicable) {
            frame.AdvanceCandidate();
            continue;
        }
        if (decision.status == CandidateStatus::Confirmed) {
            return BuildFinalSegmentResult(pens, start_pos, decision);
        }
        if (decision.status == CandidateStatus::Rejected) {
            explicitly_rejected_ts.push_back(turn.endpoint);
            if (!decision.successor_candidate_t.has_value()) {
                frame.AdvanceCandidate();
                continue;
            }

            const Point& successor = *decision.successor_candidate_t;
            if (decision.end_case == EndCase::Case2WithGap && context.audit_sink != nullptr) {
                context.audit_sink->OnCase2Cancellation(context.audit_evaluation,
                    Case2AuditCancellation{turn.endpoint, successor, decision.confirmed_at});
            }
            Require(
                successor.bar > turn.endpoint.bar,
                "A successor T-prime must be later than T."
            );
            Require(
                successor.price > turn.endpoint.price + kPriceEpsilon,
                "A canonical successor T-prime must be higher than T."
            );
            if (decision.end_case.has_value()) {
                Require(
                    *decision.end_case == EndCase::Case2WithGap
                        || *decision.end_case == EndCase::TrendA,
                    "Only ordinary innovation or Trend-A outer guard may attach "
                    "an end case to T-prime."
                );
            }

            const bool successor_already_pending = std::any_of(
                unresolved_successor_ts.begin(),
                unresolved_successor_ts.end(),
                [&successor](const Point& pending_successor) {
                    return PointsEqual(pending_successor, successor);
                }
            );
            if (!successor_already_pending) {
                unresolved_successor_ts.push_back(successor);
            }
            frame.AdvanceCandidate();
            continue;
        }

        Require(
            decision.status == CandidateStatus::Pending,
            "Candidate decision has an unknown status."
        );
        SegmentResult pending;
        pending.direction = Direction::Up;
        pending.status = SegmentStatus::Pending;
        pending.start = pens[static_cast<std::size_t>(start_pos)].start;
        pending.endpoint = turn.endpoint;
        pending.wait_reason = decision.end_case == EndCase::TrendA ? RecoveryWaitReason::TrendAWaiting
            : attempt.primary_top.has_value() ? (IntervalGap(attempt.primary_top->a, attempt.primary_top->b)
                ? RecoveryWaitReason::Case2Waiting : RecoveryWaitReason::Case1Waiting) : RecoveryWaitReason::ActiveCandidate;
        pending.explicitly_rejected_ts = explicitly_rejected_ts;
        pending.end_case = decision.end_case;
        return pending;
    }

    SegmentResult pending;
    pending.direction = Direction::Up;
    pending.status = SegmentStatus::Pending;
    pending.start = pens[static_cast<std::size_t>(start_pos)].start;
    pending.explicitly_rejected_ts = explicitly_rejected_ts;
    if (!unresolved_successor_ts.empty()) {
        const Point& terminal_successor = unresolved_successor_ts.front();
        Require(
            std::all_of(
                unresolved_successor_ts.begin(),
                unresolved_successor_ts.end(),
                [&terminal_successor](const Point& successor) {
                    return PointsEqual(successor, terminal_successor);
                }
            ),
            "End of data may not leave multiple distinct unchecked T-primes."
        );
        pending.endpoint = terminal_successor;
        pending.wait_reason = RecoveryWaitReason::SuccessorFirstReverseNotReady;
    }
    return pending;
}

// Stable frame addresses keep each feature source bound to its own retained
// range. Child completion resumes the parent at its saved candidate and event.
[[nodiscard]] SegmentResult RestoreMirroredSegmentResult(const SegmentResult& canonical);

[[nodiscard]] SegmentResult EvaluateCanonicalWithExplicitStack(
    const PenRange& root_pens,
    std::int32_t root_start_pos,
    const std::optional<Point>& root_candidate_t_not_before,
    EvaluationContext& context
) {
    // The root has a stable automatic lifetime and needs no scheduler heap
    // allocation. Only nested child frames require stable heap addresses.
    EvaluationFrame root(root_pens, root_start_pos,
        root_candidate_t_not_before, std::nullopt);
    std::vector<std::unique_ptr<EvaluationFrame>> children;
    if (context.metrics != nullptr) {
        ++context.metrics->frames_created;
        context.metrics->maximum_depth = 1U;
    }

    for (;;) {
        EvaluationFrame& frame = children.empty() ? root : *children.back();
        if (context.audit_sink != nullptr) {
            if (!frame.audit_started) {
                const PenRange& scope = frame.pens;
                frame.audit_evaluation = AuditEvaluation{
                    root_pens[static_cast<std::size_t>(root_start_pos)].start.bar,
                    root_pens[static_cast<std::size_t>(root_start_pos)].mirrored_prices ? Direction::Down : Direction::Up,
                    static_cast<std::int32_t>(children.size() + 1U),
                    scope[static_cast<std::size_t>(frame.start_pos)].start.bar,
                    scope.back().end.bar,
                    scope[static_cast<std::size_t>(frame.start_pos)].mirrored_prices ? Direction::Down : Direction::Up,
                    frame.candidate_t_not_before.has_value() ? frame.candidate_t_not_before->bar : -1,
                    frame.cache_key.has_value() ? AuditRole::NestedReaction : context.root_role,
                    context.root_role == AuditRole::RecoveryPrefix,
                };
                context.audit_sink->OnEvaluationStep(frame.audit_evaluation, AuditStep::EvaluationStarted);
                frame.audit_started = true;
            }
            context.audit_evaluation = frame.audit_evaluation;
        }
        if (frame.suspended_request.has_value()) {
            const ReactionRequest& request = *frame.suspended_request;
            Require(context.reaction_prefix_results.find(request.key) != context.reaction_prefix_results.end(),
                "A resumed parent requires its completed reaction result.");
            Require(frame.current_candidate.has_value()
                    && PointsEqual(frame.current_candidate->attempt.turn.endpoint, request.candidate)
                    && frame.current_candidate->trend.has_value()
                    && frame.current_candidate->trend->next_event_index == request.event_index
                    && frame.current_candidate->trend->awaiting_reaction,
                "A parent must resume the same candidate and reaction event.");
            frame.suspended_request.reset();
        }

        context.reaction_request.reset();
        FrameStep completed = StepCanonicalUpSegment(frame, context);
        if (completed.status == FrameStepStatus::NeedReaction) {
            Require(context.reaction_request.has_value(), "An unfinished frame must request a reaction.");
            ReactionRequest request = std::move(*context.reaction_request);
            context.reaction_request.reset();
            Require(context.reaction_prefix_results.find(request.key) == context.reaction_prefix_results.end(),
                "A reaction request must not already have a result.");
            const bool active = std::any_of(children.begin(), children.end(),
                [&request](const std::unique_ptr<EvaluationFrame>& candidate) {
                    return candidate->cache_key.has_value() && *candidate->cache_key == request.key;
                });
            Require(!active, "Nested reaction evaluation formed a cycle.");
            frame.suspended_request = request;
            children.push_back(std::make_unique<EvaluationFrame>(request.pens, 0, std::nullopt, request.key));
            if (context.metrics != nullptr) {
                ++context.metrics->frames_created;
                context.metrics->maximum_depth = std::max(context.metrics->maximum_depth, children.size() + 1U);
            }
            continue;
        }
        Require(completed.status == FrameStepStatus::Completed && completed.result.has_value(),
            "A completed frame must carry its segment result.");
        Require(!context.reaction_request.has_value(), "A completed frame may not leave a reaction request.");
        SegmentResult result = std::move(*completed.result);
        if (context.audit_sink != nullptr) {
            if (frame.audit_evaluation.direction == Direction::Down) {
                context.audit_sink->OnEvaluationCompleted(frame.audit_evaluation, RestoreMirroredSegmentResult(result));
            } else {
                context.audit_sink->OnEvaluationCompleted(frame.audit_evaluation, result);
            }
        }
        if (!frame.cache_key.has_value()) {
            Require(children.empty(), "Only the root evaluation frame may omit a cache key.");
            return result;
        }
        const ReactionPrefixKey completed_key = *frame.cache_key;
        children.pop_back();
        const bool inserted = context.reaction_prefix_results.emplace(completed_key, std::move(result)).second;
        Require(inserted, "A nested evaluation frame completed twice.");
    }
}

[[nodiscard]] Point MirrorPoint(const Point& point) noexcept {
    return Point{point.bar, -point.price};
}

[[nodiscard]] SegmentResult RestoreMirroredSegmentResult(
    const SegmentResult& canonical
) {
    SegmentResult actual;
    actual.direction = OppositeDirection(canonical.direction);
    actual.status = canonical.status;
    actual.start = MirrorPoint(canonical.start);
    if (canonical.endpoint.has_value()) {
        actual.endpoint = MirrorPoint(*canonical.endpoint);
    }
    if (canonical.intrinsic_confirmed_at.has_value()) {
        actual.intrinsic_confirmed_at = MirrorPoint(
            *canonical.intrinsic_confirmed_at
        );
    }
    if (canonical.confirmed_at.has_value()) {
        actual.confirmed_at = MirrorPoint(*canonical.confirmed_at);
    }
    for (const auto& rejected : canonical.explicitly_rejected_ts) {
        actual.explicitly_rejected_ts.push_back(MirrorPoint(rejected));
    }
    actual.wait_reason = canonical.wait_reason;
    actual.end_case = canonical.end_case;
    if (canonical.next_segment_t_not_before.has_value()) {
        actual.next_segment_t_not_before = MirrorPoint(
            *canonical.next_segment_t_not_before
        );
    }
    return actual;
}

[[nodiscard]] SegmentResult JudgeOneSegment(
    const PenRange& pens,
    const PenRange* mirrored_pens,
    std::int32_t start_pos,
    Direction direction,
    const std::optional<Point>& candidate_t_not_before,
    AuditSink* audit_sink = nullptr,
    AuditRole audit_role = AuditRole::Formal,
    EvaluationMetrics* metrics = nullptr
) {
    EvaluationContext context;
    context.audit_sink = audit_sink;
    context.root_role = audit_role;
    context.metrics = metrics;
    if (direction == Direction::Up) {
        return EvaluateCanonicalWithExplicitStack(
            pens,
            start_pos,
            candidate_t_not_before,
            context
        );
    }

    Require(
        mirrored_pens != nullptr && mirrored_pens->size() == pens.size(),
        "A down segment requires a complete mirrored Pen stream."
    );
    const SegmentResult canonical = EvaluateCanonicalWithExplicitStack(
        *mirrored_pens,
        start_pos,
        candidate_t_not_before.has_value()
            ? std::optional<Point>(MirrorPoint(*candidate_t_not_before))
            : std::nullopt,
        context
    );
    return RestoreMirroredSegmentResult(canonical);
}


}  // namespace

SegmentResult EvaluateOneSegment(
    const PenRange& pens, std::int32_t start_pos, Direction direction,
    const std::optional<Point>& candidate_t_not_before,
    AuditSink* audit_sink, AuditRole audit_role, EvaluationMetrics* metrics
) {
    if (metrics != nullptr) { *metrics = EvaluationMetrics{}; }
    const PenRange mirrored = pens.Mirrored();
    return JudgeOneSegment(pens, &mirrored, start_pos, direction,
                           candidate_t_not_before, audit_sink, audit_role, metrics);
}

}  // namespace tdx_segment::detail
