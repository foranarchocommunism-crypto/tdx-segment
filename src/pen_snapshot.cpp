#include "pen_snapshot.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace tdx_segment::detail {

double Pen::low() const noexcept {
    return std::min(start.price, end.price);
}

double Pen::high() const noexcept {
    return std::max(start.price, end.price);
}

bool Pen::has_price_path() const noexcept {
    return price_store != nullptr
        && start.bar >= 0
        && end.bar >= start.bar
        && static_cast<std::size_t>(end.bar) < price_store->size();
}

PriceBar Pen::price_bar_at(std::int32_t bar) const {
    if (
        !has_price_path()
        || bar < start.bar
        || bar > end.bar
    ) {
        throw std::out_of_range("Price bar is outside the Pen path.");
    }
    const PriceBar& source = (*price_store)[static_cast<std::size_t>(bar)];
    if (!mirrored_prices) {
        return source;
    }
    return PriceBar{source.bar, -source.low, -source.high};
}

namespace {

[[nodiscard]] bool PointsEqual(const Point& left, const Point& right) noexcept {
    return left.bar == right.bar
        && std::abs(left.price - right.price) <= kPriceEpsilon;
}

struct Extrema {
    double low;
    double high;
};

[[nodiscard]] Extrema ScanIntrinsicExtrema(const Pen& pen) {
    Extrema result{pen.low(), pen.high()};
    if (pen.has_price_path()) {
        for (std::int32_t bar = pen.start.bar; bar <= pen.end.bar; ++bar) {
            const PriceBar value = pen.price_bar_at(bar);
            result.low = std::min(result.low, value.low);
            result.high = std::max(result.high, value.high);
        }
    }
    return result;
}

}  // namespace

void CachePenIntrinsicExtrema(Pen& pen) {
    const Extrema extrema = ScanIntrinsicExtrema(pen);
    pen.cached_path_low = extrema.low;
    pen.cached_path_high = extrema.high;
}

double PenIntrinsicLow(const Pen& pen) {
    return std::isnan(pen.cached_path_low)
        ? ScanIntrinsicExtrema(pen).low
        : pen.cached_path_low;
}

double PenIntrinsicHigh(const Pen& pen) {
    return std::isnan(pen.cached_path_high)
        ? ScanIntrinsicExtrema(pen).high
        : pen.cached_path_high;
}

PenRange::PenRange(const std::vector<Pen>& pens) noexcept
    : pens_(&pens), end_(pens.size()) {}

PenRange::PenRange(
    const std::vector<Pen>& pens,
    std::size_t begin,
    std::size_t end,
    bool mirrored
)
    : pens_(&pens), begin_(begin), end_(end), mirrored_(mirrored) {
    if (begin > end || end > pens.size()) {
        throw std::out_of_range("Pen range exceeds its snapshot.");
    }
}

std::size_t PenRange::size() const noexcept { return end_ - begin_; }
bool PenRange::empty() const noexcept { return begin_ == end_; }
bool PenRange::mirrored() const noexcept { return mirrored_; }
bool PenRange::validated() const noexcept { return validated_; }

Pen PenRange::operator[](std::size_t index) const noexcept {
    Pen result = (*pens_)[begin_ + index];
    result.position = static_cast<std::int32_t>(index);
    if (mirrored_) {
        result.start.price = -result.start.price;
        result.end.price = -result.end.price;
        result.direction = result.direction == Direction::Up
            ? Direction::Down : Direction::Up;
        result.mirrored_prices = !result.mirrored_prices;
        const double previous_low = result.cached_path_low;
        result.cached_path_low = -result.cached_path_high;
        result.cached_path_high = -previous_low;
    }
    return result;
}

Pen PenRange::at(std::size_t index) const {
    if (index >= size()) {
        throw std::out_of_range("Pen range index is out of bounds.");
    }
    return (*this)[index];
}

Pen PenRange::front() const { return at(0U); }
Pen PenRange::back() const { return at(empty() ? 0U : size() - 1U); }

std::int32_t PenRange::global_position_at(std::size_t index) const {
    return (*pens_)[snapshot_index_at(index)].position;
}

std::size_t PenRange::snapshot_index_at(std::size_t index) const {
    if (index >= size()) {
        throw std::out_of_range("Pen range index is out of bounds.");
    }
    return begin_ + index;
}

PenRange PenRange::Subrange(std::size_t begin, std::size_t end) const {
    if (begin > end || end > size()) {
        throw std::out_of_range("Pen subrange exceeds its visible range.");
    }
    if (pens_ == nullptr) {
        return PenRange{};
    }
    PenRange result{*pens_, begin_ + begin, begin_ + end, mirrored_};
    result.validated_ = validated_;
    return result;
}

PenRange PenRange::Prefix(std::size_t count) const { return Subrange(0U, count); }

PenRange PenRange::Mirrored() const noexcept {
    PenRange result = *this;
    result.mirrored_ = !result.mirrored_;
    return result;
}

PenRange::const_iterator PenRange::begin() const noexcept {
    return const_iterator{this, 0U};
}

PenRange::const_iterator PenRange::end() const noexcept {
    return const_iterator{this, size()};
}

PenRange::const_iterator::const_iterator(
    const PenRange* range, std::size_t index
) noexcept : range_(range), index_(index) {}

Pen PenRange::const_iterator::operator*() const noexcept { return (*range_)[index_]; }
PenRange::const_iterator& PenRange::const_iterator::operator++() noexcept {
    ++index_;
    return *this;
}
PenRange::const_iterator PenRange::const_iterator::operator++(int) noexcept {
    const_iterator previous = *this;
    ++*this;
    return previous;
}
bool PenRange::const_iterator::operator==(const const_iterator& other) const noexcept {
    return range_ == other.range_ && index_ == other.index_;
}
bool PenRange::const_iterator::operator!=(const const_iterator& other) const noexcept {
    return !(*this == other);
}

bool ValidatePenRange(const PenRange& pens) noexcept {
    if (pens.validated()) {
        return true;
    }
    for (std::size_t index = 0; index < pens.size(); ++index) {
        const Pen& pen = pens[index];
        if (pens.global_position_at(index) != static_cast<std::int32_t>(pens.snapshot_index_at(index))) {
            return false;
        }
        if (pen.start.bar >= pen.end.bar) {
            return false;
        }
        if (
            pen.direction == Direction::Up
            && pen.end.price <= pen.start.price + kPriceEpsilon
        ) {
            return false;
        }
        if (
            pen.direction == Direction::Down
            && pen.end.price >= pen.start.price - kPriceEpsilon
        ) {
            return false;
        }

        if (pen.price_store != nullptr) {
            if (
                !pen.has_price_path()
                || pen.price_bar_at(pen.start.bar).bar != pen.start.bar
                || pen.price_bar_at(pen.end.bar).bar != pen.end.bar
            ) {
                return false;
            }
            std::int32_t previous_bar = -1;
            for (
                std::int32_t bar_index = pen.start.bar;
                bar_index <= pen.end.bar;
                ++bar_index
            ) {
                const PriceBar bar = pen.price_bar_at(bar_index);
                if (!std::isfinite(bar.high) || !std::isfinite(bar.low)) {
                    return false;
                }
                if (bar.high < bar.low) {
                    return false;
                }
                if (bar.bar < pen.start.bar || bar.bar > pen.end.bar) {
                    return false;
                }
                if (previous_bar >= 0 && bar.bar <= previous_bar) {
                    return false;
                }
                previous_bar = bar.bar;
            }
        }

        if (index == 0U) {
            continue;
        }
        const Pen& previous = pens[index - 1U];
        if (previous.direction == pen.direction) {
            return false;
        }
        if (!PointsEqual(previous.end, pen.start)) {
            return false;
        }
    }
    return true;
}

DecodedSnapshot::DecodedSnapshot(std::vector<PriceBar> price_bars, std::vector<Pen> pens)
    : price_bars_(std::move(price_bars)), pens_(std::move(pens)) {
    RebindPriceStore();
    for (Pen& pen : pens_) {
        CachePenIntrinsicExtrema(pen);
    }
}

DecodedSnapshot::DecodedSnapshot(
    std::vector<PriceBar> price_bars,
    std::vector<Pen> pens,
    DecoderValidatedInput
) : DecodedSnapshot(std::move(price_bars), std::move(pens)) {
    validated_ = true;
}

DecodedSnapshot::DecodedSnapshot(DecodedSnapshot&& other) noexcept
    : price_bars_(std::move(other.price_bars_)), pens_(std::move(other.pens_)),
      validated_(std::exchange(other.validated_, false)) {
    RebindPriceStore();
}

DecodedSnapshot& DecodedSnapshot::operator=(DecodedSnapshot&& other) noexcept {
    if (this != &other) {
        price_bars_ = std::move(other.price_bars_);
        pens_ = std::move(other.pens_);
        validated_ = std::exchange(other.validated_, false);
        RebindPriceStore();
    }
    return *this;
}

void DecodedSnapshot::RebindPriceStore() noexcept {
    for (Pen& pen : pens_) {
        pen.price_store = price_bars_.empty() ? nullptr : &price_bars_;
    }
}

const std::vector<PriceBar>& DecodedSnapshot::PriceBars() const noexcept {
    return price_bars_;
}
const std::vector<Pen>& DecodedSnapshot::Pens() const noexcept { return pens_; }
PenRange DecodedSnapshot::Range() const noexcept {
    PenRange result{pens_};
    result.validated_ = validated_;
    return result;
}

bool DecodedSnapshot::Validate() noexcept {
    if (!validated_) {
        validated_ = ValidatePenRange(Range());
    }
    return validated_;
}

}  // namespace tdx_segment::detail
