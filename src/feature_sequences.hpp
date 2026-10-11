#pragma once

#include "pen_snapshot.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <map>
#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>

namespace tdx_segment::detail {

[[nodiscard]] bool ThreePensDefineSegment(
    const PenRange& pens,
    std::int32_t start_pos,
    Direction direction
) noexcept;
[[nodiscard]] bool ThreePensMeetStrictInitialProgress(
    const PenRange& pens,
    std::int32_t start_pos,
    Direction direction
) noexcept;

// This private seam keeps normalization and its synchronized indices together.
inline void FeatureRequire(bool condition, const char* message) {
    if (!condition) {
        throw std::logic_error(message);
    }
}

// 标准元素只能由原始特征序列中相邻的同向笔合并而成。
// 合法 Pen 流严格方向交替，所以其来源全局编号必为步长 2 的连续区间。
struct FeatureSourceSpan {
    std::int32_t first_position{-1};
    std::int32_t last_position{-1};

    [[nodiscard]] bool contains(std::int32_t position) const noexcept {
        return first_position >= 0
            && position >= first_position
            && position <= last_position
            && (position - first_position) % 2 == 0;
    }
};

struct FeatureElement {
    double low{};
    double high{};
    FeatureSourceSpan source_pen_span{};
    std::int32_t first_bar{-1};
    std::int32_t last_bar{-1};
};

[[nodiscard]] inline double IntervalLow(const Pen& pen) noexcept {
    return pen.low();
}

[[nodiscard]] inline double IntervalHigh(const Pen& pen) noexcept {
    return pen.high();
}

[[nodiscard]] inline double IntervalLow(const FeatureElement& element) noexcept {
    return element.low;
}

[[nodiscard]] inline double IntervalHigh(const FeatureElement& element) noexcept {
    return element.high;
}

template <typename Left, typename Right>
[[nodiscard]] bool IntervalOverlap(
    const Left& left,
    const Right& right
) noexcept {
    return std::max(IntervalLow(left), IntervalLow(right))
        <= std::min(IntervalHigh(left), IntervalHigh(right))
            + kPriceEpsilon;
}

template <typename Left, typename Right>
[[nodiscard]] bool IntervalGap(
    const Left& left,
    const Right& right
) noexcept {
    return std::max(IntervalLow(left), IntervalLow(right))
        > std::min(IntervalHigh(left), IntervalHigh(right))
            + kPriceEpsilon;
}

template <typename Left, typename Right>
[[nodiscard]] bool IntervalContains(
    const Left& left,
    const Right& right
) noexcept {
    return IntervalLow(left) <= IntervalLow(right) + kPriceEpsilon
        && IntervalHigh(left) + kPriceEpsilon >= IntervalHigh(right);
}

template <typename Left, typename Right>
[[nodiscard]] bool HasContainment(
    const Left& left,
    const Right& right
) noexcept {
    return IntervalContains(left, right) || IntervalContains(right, left);
}

[[nodiscard]] FeatureElement PenToFeature(const Pen& pen);
[[nodiscard]] FeatureElement MergeContainedElements(
    const FeatureElement& left,
    const FeatureElement& right,
    Direction feature_pen_direction
);
std::size_t AppendStandardizedFeature(
    std::vector<FeatureElement>& result,
    const FeatureElement& current,
    Direction feature_pen_direction
);

class SecondaryHighestHighTracker {
public:
    void SyncFrom(
        const std::vector<FeatureElement>& standardized,
        std::size_t changed_from
    ) {
        FeatureRequire(
            !standardized.empty() && changed_from < standardized.size(),
            "A secondary highest-high tracker received an invalid suffix."
        );
        if (changed_from > 0U) {
            FeatureRequire(
                highest_values_.size() >= changed_from
                    && selected_indices_.size() >= changed_from,
                "An unchanged highest-high prefix is missing."
            );
        }
        highest_values_.resize(standardized.size());
        selected_indices_.resize(standardized.size());

        for (std::size_t index = changed_from;
             index < standardized.size(); ++index) {
            if (index == 0U) {
                highest_values_[index] = standardized[index].high;
                selected_indices_[index] = index;
                continue;
            }

            const double previous_highest = highest_values_[index - 1U];
            if (standardized[index].high > previous_highest) {
                highest_values_[index] = standardized[index].high;
                selected_indices_[index] = index;
                continue;
            }

            highest_values_[index] = previous_highest;
            selected_indices_[index] =
                previous_highest - standardized[index].high
                    <= kPriceEpsilon
                ? index
                : selected_indices_[index - 1U];
        }
    }

    [[nodiscard]] std::optional<std::size_t> SelectBeforeB(
        std::size_t b_index
    ) const {
        if (b_index == 0U) {
            return std::nullopt;
        }
        FeatureRequire(
            b_index - 1U < selected_indices_.size(),
            "A secondary B2 index exceeds the tracked standardized prefix."
        );
        return selected_indices_[b_index - 1U];
    }

private:
    std::vector<double> highest_values_;
    std::vector<std::size_t> selected_indices_;
};

// ENG-034：包含回溯只更新受影响的尾部；区间最大值查询不重扫左侧。
class PrefixRangeMaximum {
public:
    void SyncFrom(
        const std::vector<FeatureElement>& values,
        std::size_t changed_from
    ) {
        if (values.size() > leaf_capacity_) {
            while (leaf_capacity_ < values.size()) {
                leaf_capacity_ *= 2U;
            }
            tree_.assign(2U * leaf_capacity_, EmptyValue());
            for (std::size_t index = 0; index < values.size(); ++index) {
                tree_[leaf_capacity_ + index] = values[index].high;
            }
            for (std::size_t node = leaf_capacity_ - 1U; node > 0U; --node) {
                tree_[node] = std::max(tree_[2U * node], tree_[2U * node + 1U]);
            }
        } else {
            const std::size_t affected_end = std::max(size_, values.size());
            for (std::size_t index = changed_from; index < affected_end; ++index) {
                std::size_t node = leaf_capacity_ + index;
                tree_[node] = index < values.size() ? values[index].high : EmptyValue();
                while (node > 1U) {
                    node /= 2U;
                    tree_[node] = std::max(tree_[2U * node], tree_[2U * node + 1U]);
                }
            }
        }
        size_ = values.size();
    }

    [[nodiscard]] double Query(std::size_t begin, std::size_t end) const {
        FeatureRequire(begin < end && end <= size_, "Invalid primary-left maximum range.");
        double maximum = EmptyValue();
        begin += leaf_capacity_;
        end += leaf_capacity_;
        while (begin < end) {
            if ((begin & 1U) != 0U) {
                maximum = std::max(maximum, tree_[begin++]);
            }
            if ((end & 1U) != 0U) {
                maximum = std::max(maximum, tree_[--end]);
            }
            begin /= 2U;
            end /= 2U;
        }
        return maximum;
    }

private:
    [[nodiscard]] static double EmptyValue() noexcept {
        return -std::numeric_limits<double>::infinity();
    }

    std::size_t leaf_capacity_{1U};
    std::size_t size_{};
    std::vector<double> tree_ = std::vector<double>(2U, EmptyValue());
};

// SPEC-034：默认查询最近合格标准元素；完整未创新高形态才查询旧最高 A。
// 包含回溯、有序价格索引和区间索引由同一个内部模块同步维护。
class PrimaryLeftPrefix {
public:
    PrimaryLeftPrefix() = default;
    PrimaryLeftPrefix(const PrimaryLeftPrefix&) = delete;
    PrimaryLeftPrefix& operator=(const PrimaryLeftPrefix&) = delete;
    PrimaryLeftPrefix(PrimaryLeftPrefix&&) noexcept = default;
    PrimaryLeftPrefix& operator=(PrimaryLeftPrefix&&) noexcept = default;

    [[nodiscard]] const FeatureElement* NearestStandard() const noexcept {
        return standardized_.empty() ? nullptr : &standardized_.back();
    }

    void Append(const FeatureElement& current) {
        const std::size_t changed_from = AppendStandardizedFeature(
            standardized_,
            current,
            Direction::Down
        );
        for (std::size_t index = changed_from;
             index < ordered_entries_.size(); ++index) {
            by_start_high_.erase(ordered_entries_[index]);
        }
        ordered_entries_.resize(changed_from);

        for (std::size_t index = changed_from;
             index < standardized_.size(); ++index) {
            ordered_entries_.push_back(
                by_start_high_.emplace(standardized_[index].high, index)
            );
        }
        range_maximum_.SyncFrom(standardized_, changed_from);
        FeatureRequire(
            !standardized_.empty()
                && ordered_entries_.size() == standardized_.size(),
            "A primary-left append must synchronize its ordered index."
        );
    }

    [[nodiscard]] const FeatureElement* CurrentA(
        double candidate_price,
        const Pen& first_reverse_pen
    ) const {
        if (standardized_.empty()) {
            return nullptr;
        }
        const FeatureElement& nearest = standardized_.back();
        const FeatureElement* highest = HighestEligibleA(candidate_price);
        if (highest != nullptr && highest != &nearest) {
            const auto highest_index = static_cast<std::size_t>(
                highest - standardized_.data()
            );
            if (
                range_maximum_.Query(highest_index + 1U, standardized_.size())
                    < highest->high - kPriceEpsilon
                && candidate_price > highest->high + kPriceEpsilon
                && IntervalOverlap(first_reverse_pen, *highest)
            ) {
                return highest;
            }
        }
        // 最近元素不合格仍保留 T；不能退回一个更早、更低的元素。
        return nearest.high <= candidate_price + kPriceEpsilon ? &nearest : nullptr;
    }

private:
    // BASIS-032：旧过滤、最高值及 EPS 并列选法原样保留在特例查询中。
    [[nodiscard]] const FeatureElement* HighestEligibleA(
        double candidate_price
    ) const {
        auto after_eligible = by_start_high_.upper_bound(
            candidate_price + kPriceEpsilon
        );
        if (after_eligible == by_start_high_.begin()) {
            return nullptr;
        }

        auto highest = after_eligible;
        --highest;
        const double highest_price = highest->first;
        std::size_t selected_index = highest->second;
        const auto tied_begin = by_start_high_.lower_bound(
            highest_price - kPriceEpsilon
        );
        for (auto item = tied_begin; item != after_eligible; ++item) {
            selected_index = std::max(selected_index, item->second);
        }
        FeatureRequire(
            selected_index < standardized_.size(),
            "A primary-left ordered index selected an invalid element."
        );
        return &standardized_[selected_index];
    }

    using OrderedMap = std::multimap<double, std::size_t>;
    std::vector<FeatureElement> standardized_;
    OrderedMap by_start_high_;
    std::vector<OrderedMap::iterator> ordered_entries_;
    PrefixRangeMaximum range_maximum_;
};

}  // namespace tdx_segment::detail
