#include "feature_sequences.hpp"

namespace tdx_segment::detail {

bool ThreePensDefineSegment(
    const PenRange& pens,
    std::int32_t start_pos,
    Direction direction
) noexcept {
    if (
        start_pos < 0
        || start_pos + 2 >= static_cast<std::int32_t>(pens.size())
    ) {
        return false;
    }
    const Pen& first = pens[static_cast<std::size_t>(start_pos)];
    const Pen& second = pens[static_cast<std::size_t>(start_pos + 1)];
    const Pen& third = pens[static_cast<std::size_t>(start_pos + 2)];
    const Direction opposite_direction = direction == Direction::Up
        ? Direction::Down : Direction::Up;
    if (
        first.direction != direction
        || second.direction != opposite_direction
        || third.direction != direction
    ) {
        return false;
    }
    const double overlap_low = std::max({
        first.low(),
        second.low(),
        third.low(),
    });
    const double overlap_high = std::min({
        first.high(),
        second.high(),
        third.high(),
    });
    if (overlap_low > overlap_high + kPriceEpsilon) {
        return false;
    }
    // 已知起点处三笔只用来建立方向交替和公共重叠的基础结构。第三笔
    // 可以不相对第一笔创新高/创新低；真正的候选线段仍由后续
    // 第一/第二特征序列和笔破坏规则确认。
    return true;
}

bool ThreePensMeetStrictInitialProgress(
    const PenRange& pens,
    std::int32_t start_pos,
    Direction direction
) noexcept {
    if (!ThreePensDefineSegment(pens, start_pos, direction)) {
        return false;
    }
    const Pen& first = pens[static_cast<std::size_t>(start_pos)];
    const Pen& second = pens[static_cast<std::size_t>(start_pos + 1)];
    const Pen& third = pens[static_cast<std::size_t>(start_pos + 2)];
    const double sign = direction == Direction::Up ? 1.0 : -1.0;
    return sign * (second.end.price - first.start.price) > kPriceEpsilon
        && sign * (third.end.price - first.end.price) > kPriceEpsilon;
}

[[nodiscard]] FeatureElement PenToFeature(const Pen& pen) {
    return FeatureElement{
        pen.low(),
        pen.high(),
        FeatureSourceSpan{pen.position, pen.position},
        pen.start.bar,
        pen.end.bar,
    };
}

[[nodiscard]] FeatureElement MergeContainedElements(
    const FeatureElement& left,
    const FeatureElement& right,
    Direction feature_pen_direction
) {
    FeatureRequire(
        HasContainment(left, right),
        "Only contained feature elements may be merged."
    );

    FeatureElement merged;
    if (feature_pen_direction == Direction::Down) {
        merged.low = std::max(left.low, right.low);
        merged.high = std::max(left.high, right.high);
    } else {
        merged.low = std::min(left.low, right.low);
        merged.high = std::min(left.high, right.high);
    }
    FeatureRequire(
        left.source_pen_span.first_position >= 0
            && left.source_pen_span.last_position + 2
                == right.source_pen_span.first_position,
        "Merged feature sources must be adjacent in the raw feature sequence."
    );
    merged.source_pen_span = FeatureSourceSpan{
        left.source_pen_span.first_position,
        right.source_pen_span.last_position,
    };
    merged.first_bar = std::min(left.first_bar, right.first_bar);
    merged.last_bar = std::max(left.last_bar, right.last_bar);
    return merged;
}

std::size_t AppendStandardizedFeature(
    std::vector<FeatureElement>& result,
    const FeatureElement& current,
    Direction feature_pen_direction
) {
    if (result.empty() || !HasContainment(result.back(), current)) {
        const std::size_t changed_from = result.size();
        result.push_back(current);
        return changed_from;
    }

    std::size_t changed_from = result.size() - 1U;
    result.back() = MergeContainedElements(
        result.back(),
        current,
        feature_pen_direction
    );
    while (
        result.size() >= 2U
        && HasContainment(
            result[result.size() - 2U],
            result.back()
        )
    ) {
        FeatureElement merged = MergeContainedElements(
            result[result.size() - 2U],
            result.back(),
            feature_pen_direction
        );
        changed_from = std::min(changed_from, result.size() - 2U);
        result.pop_back();
        result.back() = std::move(merged);
    }
    return changed_from;
}

}  // namespace tdx_segment::detail
