#pragma once

#include "segment_types.hpp"

#include <cstddef>
#include <iterator>
#include <vector>

namespace tdx_segment::detail {

struct DecodeResult;

// Values use the Pen's current orientation, including mirrored price paths.
// Manual Pen streams may leave caches empty; queries then scan their own path.
void CachePenIntrinsicExtrema(Pen& pen);
[[nodiscard]] double PenIntrinsicLow(const Pen& pen);
[[nodiscard]] double PenIntrinsicHigh(const Pen& pen);

// A bounded borrowed view. Its owner must outlive the range and its iterators.
// Metadata is copied only for the queried Pen; slicing/mirroring allocate nothing.
class PenRange {
public:
    class const_iterator {
    public:
        using iterator_category = std::forward_iterator_tag;
        using value_type = Pen;
        using difference_type = std::ptrdiff_t;
        using pointer = void;
        using reference = Pen;

        const_iterator() noexcept = default;
        [[nodiscard]] Pen operator*() const noexcept;
        const_iterator& operator++() noexcept;
        const_iterator operator++(int) noexcept;
        [[nodiscard]] bool operator==(const const_iterator& other) const noexcept;
        [[nodiscard]] bool operator!=(const const_iterator& other) const noexcept;

    private:
        friend class PenRange;
        const_iterator(const PenRange* range, std::size_t index) noexcept;
        const PenRange* range_{};
        std::size_t index_{};
    };

    PenRange() noexcept = default;
    explicit PenRange(const std::vector<Pen>& pens) noexcept;
    PenRange(
        const std::vector<Pen>& pens,
        std::size_t begin,
        std::size_t end,
        bool mirrored = false
    );

    [[nodiscard]] std::size_t size() const noexcept;
    [[nodiscard]] bool empty() const noexcept;
    [[nodiscard]] bool mirrored() const noexcept;
    [[nodiscard]] bool validated() const noexcept;
    [[nodiscard]] Pen operator[](std::size_t index) const noexcept;
    [[nodiscard]] Pen at(std::size_t index) const;
    [[nodiscard]] Pen front() const;
    [[nodiscard]] Pen back() const;
    [[nodiscard]] std::int32_t global_position_at(std::size_t index) const;
    [[nodiscard]] std::size_t snapshot_index_at(std::size_t index) const;
    [[nodiscard]] PenRange Subrange(std::size_t begin, std::size_t end) const;
    [[nodiscard]] PenRange Prefix(std::size_t count) const;
    [[nodiscard]] PenRange Mirrored() const noexcept;
    [[nodiscard]] const_iterator begin() const noexcept;
    [[nodiscard]] const_iterator end() const noexcept;

private:
    friend class DecodedSnapshot;
    const std::vector<Pen>* pens_{};
    std::size_t begin_{};
    std::size_t end_{};
    bool mirrored_{};
    bool validated_{};
};

// Trusted snapshot ranges reuse their owner's successful validation. Borrowed
// manual ranges always validate source numbering, continuity and price paths.
[[nodiscard]] bool ValidatePenRange(const PenRange& pens) noexcept;

// One call owns one immutable decoded snapshot. Moving the owner invalidates
// borrowed ranges and explicitly rebinds Pen path pointers to the new vector.
class DecodedSnapshot {
public:
    DecodedSnapshot() = default;
    DecodedSnapshot(std::vector<PriceBar> price_bars, std::vector<Pen> pens);
    DecodedSnapshot(const DecodedSnapshot&) = delete;
    DecodedSnapshot& operator=(const DecodedSnapshot&) = delete;
    DecodedSnapshot(DecodedSnapshot&& other) noexcept;
    DecodedSnapshot& operator=(DecodedSnapshot&& other) noexcept;

    [[nodiscard]] const std::vector<PriceBar>& PriceBars() const noexcept;
    [[nodiscard]] const std::vector<Pen>& Pens() const noexcept;
    [[nodiscard]] PenRange Range() const noexcept;
    [[nodiscard]] bool Validate() noexcept;

private:
    // Only the decoder may assert these invariants. Manual constructors retain
    // full ValidatePenRange checks; there is no setter for granting trust.
    friend DecodeResult DecodePens(const TdxSeriesView& input);
    struct DecoderValidatedInput {};
    DecodedSnapshot(
        std::vector<PriceBar> price_bars,
        std::vector<Pen> pens,
        DecoderValidatedInput
    );
    void RebindPriceStore() noexcept;
    std::vector<PriceBar> price_bars_;
    std::vector<Pen> pens_;
    bool validated_{};
};

}  // namespace tdx_segment::detail
