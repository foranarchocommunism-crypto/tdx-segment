#include "../src/pen_snapshot.hpp"
#include "../src/feature_sequences.hpp"

#if defined(NDEBUG)
#undef NDEBUG
#endif
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace {
using namespace tdx_segment::detail;

template <typename Operation>
void ExpectOutOfRange(Operation operation) {
    bool threw = false;
    try {
        operation();
    } catch (const std::out_of_range&) {
        threw = true;
    }
    assert(threw);
}

Pen ManualPen(
    std::int32_t position, std::int32_t start_bar,
    double start_price, double end_price
) {
    Pen pen;
    pen.position = position;
    pen.start = Point{start_bar, start_price};
    pen.end = Point{start_bar + 1, end_price};
    pen.direction = end_price > start_price ? Direction::Up : Direction::Down;
    return pen;
}

DecodedSnapshot MakeSnapshot(std::int32_t first_position = 100) {
    std::vector<PriceBar> prices{
        {0, 3, -1}, {1, 10, 2}, {2, 7, 3}, {3, 14, 5}, {4, 4, 0},
    };
    std::vector<Pen> pens{
        ManualPen(first_position, 0, 2, 8),
        ManualPen(first_position + 1, 1, 8, 4),
        ManualPen(first_position + 2, 2, 4, 11),
        ManualPen(first_position + 3, 3, 11, 3),
    };
    return DecodedSnapshot{std::move(prices), std::move(pens)};
}

void CheckSamePen(const Pen& left, const Pen& right) {
    assert(left.position == right.position);
    assert(left.start.bar == right.start.bar && left.end.bar == right.end.bar);
    assert(left.start.price == right.start.price && left.end.price == right.end.price);
    assert(left.direction == right.direction);
    assert(left.price_store == right.price_store);
    assert(left.mirrored_prices == right.mirrored_prices);
    assert(left.cached_path_low == right.cached_path_low);
    assert(left.cached_path_high == right.cached_path_high);
}

void CheckSnapshotOwner(const DecodedSnapshot& snapshot) {
    assert(snapshot.Pens().size() == 4U);
    for (const Pen& pen : snapshot.Pens()) {
        assert(pen.price_store == &snapshot.PriceBars());
        assert(pen.has_price_path());
    }
}

void CheckManualValidation() {
    std::vector<Pen> pens{
        ManualPen(0, 0, 2, 8), ManualPen(1, 1, 8, 4),
        ManualPen(2, 2, 4, 11), ManualPen(3, 3, 11, 3),
    };
    const PenRange range{pens};
    assert(!range.validated());
    assert(ValidatePenRange(range));
    assert(ValidatePenRange(range.Subrange(1U, 3U)));
    assert(!range.Subrange(1U, 3U).validated());
    assert(ValidatePenRange(range.Mirrored()));
    assert(!range.Mirrored().validated());

    // Range-local renumbering cannot hide an invalid source position. A manual
    // range keeps checking on subsequent calls after its source is changed.
    pens[1U].position = 0;
    assert(!ValidatePenRange(range));
    assert(!ValidatePenRange(range.Subrange(1U, 3U)));
    pens[1U].position = 1;
    assert(ValidatePenRange(range));

    const auto invalid_change = [&](auto change) {
        std::vector<Pen> invalid = pens;
        change(invalid);
        assert(!ValidatePenRange(PenRange{invalid}));
        assert(!ValidatePenRange(PenRange{invalid}.Mirrored()));
    };
    invalid_change([](auto& values) { values[0U].end.bar = values[0U].start.bar; });
    invalid_change([](auto& values) { values[0U].end.price = values[0U].start.price; });
    invalid_change([](auto& values) { values[1U].end.price = values[1U].start.price; });
    invalid_change([](auto& values) {
        values[1U].end.price = 9;
        values[1U].direction = Direction::Up;
    });
    invalid_change([](auto& values) { ++values[1U].start.bar; });
    invalid_change([](auto& values) { values[1U].start.price += 2 * kPriceEpsilon; });

    std::vector<PriceBar> prices{{0, 4, 1}, {1, 7, 2}, {2, 8, 3}, {3, 10, 4}};
    Pen long_pen = ManualPen(0, 0, 2, 8);
    long_pen.end.bar = 3;
    long_pen.price_store = &prices;
    std::vector<Pen> long_pens{long_pen};
    const PenRange long_range{long_pens};
    assert(ValidatePenRange(long_range));
    const auto invalid_price = [&](auto change) {
        std::vector<PriceBar> invalid_prices = prices;
        change(invalid_prices);
        long_pens[0U].price_store = &invalid_prices;
        assert(!ValidatePenRange(long_range));
        assert(!ValidatePenRange(long_range.Mirrored()));
        long_pens[0U].price_store = &prices;
    };
    invalid_price([](auto& values) { values[0U].bar = 1; });
    invalid_price([](auto& values) { values[3U].bar = 2; });
    invalid_price([](auto& values) { values[1U].bar = 0; });
    invalid_price([](auto& values) { values[1U].bar = 4; });
    invalid_price([](auto& values) {
        values[1U].high = std::numeric_limits<double>::quiet_NaN();
    });
    invalid_price([](auto& values) {
        values[1U].low = std::numeric_limits<double>::infinity();
    });
    invalid_price([](auto& values) { values[1U].high = values[1U].low - 1; });
    long_pens[0U].end.bar = 4;
    assert(!ValidatePenRange(long_range));
    assert(!ValidatePenRange(long_range.Mirrored()));
}

void CheckTrustedSnapshots() {
    DecodedSnapshot snapshot = MakeSnapshot(0);
    const PenRange before_validation = snapshot.Range();
    assert(!before_validation.validated());
    assert(snapshot.Validate());
    assert(snapshot.Validate());
    assert(!before_validation.validated());
    const PenRange trusted = snapshot.Range();
    assert(trusted.validated() && ValidatePenRange(trusted));
    const PenRange bounded = trusted.Subrange(1U, 4U).Prefix(2U).Mirrored();
    assert(bounded.validated() && ValidatePenRange(bounded));
    assert(bounded[0U].position == 0 && bounded.global_position_at(0U) == 1);
    assert(bounded.snapshot_index_at(1U) == 2U);
    assert(bounded.Mirrored().validated());
    assert(trusted.Prefix(0U).validated());
    assert(trusted.Subrange(4U, 4U).validated());
    const PenRange explicitly_borrowed{snapshot.Pens()};
    assert(!explicitly_borrowed.validated() && ValidatePenRange(explicitly_borrowed));

    DecodedSnapshot moved{std::move(snapshot)};
    CheckSnapshotOwner(moved);
    assert(moved.Range().validated() && ValidatePenRange(moved.Range()));
    assert(!snapshot.Range().validated());
    DecodedSnapshot assigned = MakeSnapshot();
    assert(!assigned.Validate() && !assigned.Range().validated());
    assigned = std::move(moved);
    CheckSnapshotOwner(assigned);
    assert(assigned.Range().validated() && ValidatePenRange(assigned.Range()));
    assert(!moved.Range().validated());

    // Moving an unchecked owner into a checked one must revoke the old trust.
    DecodedSnapshot unchecked = MakeSnapshot(0);
    assigned = std::move(unchecked);
    assert(!assigned.Range().validated() && !unchecked.Range().validated());
    assert(assigned.Validate() && assigned.Range().validated());
    DecodedSnapshot invalid = MakeSnapshot();
    assigned = std::move(invalid);
    assert(!assigned.Validate() && !assigned.Range().validated());
    assert(!invalid.Range().validated());

    DecodedSnapshot empty_snapshot;
    assert(!empty_snapshot.Range().validated());
    assert(empty_snapshot.Validate() && empty_snapshot.Range().validated());
    assert(empty_snapshot.Range().Subrange(0U, 0U).validated());
    assert(empty_snapshot.Range().Mirrored().validated());
}

void CheckInitialStructureFunctions() {
    std::vector<Pen> pens{
        ManualPen(0, 0, 0, 5), ManualPen(1, 1, 5, 2), ManualPen(2, 2, 2, 7),
    };
    const PenRange range{pens};
    assert(ThreePensDefineSegment(range, 0, Direction::Up));
    assert(ThreePensMeetStrictInitialProgress(range, 0, Direction::Up));
    assert(ThreePensDefineSegment(range.Mirrored(), 0, Direction::Down));
    assert(ThreePensMeetStrictInitialProgress(range.Mirrored(), 0, Direction::Down));
    assert(!ThreePensDefineSegment(range, -1, Direction::Up));
    assert(!ThreePensDefineSegment(range, 1, Direction::Up));
    assert(!ThreePensDefineSegment(range.Prefix(2U), 0, Direction::Up));
    assert(!ThreePensDefineSegment(range, 0, Direction::Down));

    // Known-start structural eligibility deliberately allows a third Pen that
    // makes no new extreme; the strict search for an initial start does not.
    pens[2U].end.price = 5;
    assert(ThreePensDefineSegment(range, 0, Direction::Up));
    assert(!ThreePensMeetStrictInitialProgress(range, 0, Direction::Up));
    assert(!ThreePensMeetStrictInitialProgress(range.Mirrored(), 0, Direction::Down));
    pens[2U].end.price = 7;
    pens[1U].end.price = 0;
    pens[2U].start.price = 0;
    assert(ThreePensDefineSegment(range, 0, Direction::Up));
    assert(!ThreePensMeetStrictInitialProgress(range, 0, Direction::Up));
    pens[1U].end.price = -1;
    pens[2U].start.price = -1;
    pens[2U].end.price = -0.5;
    assert(!ThreePensDefineSegment(range, 0, Direction::Up));
    assert(!ThreePensDefineSegment(range.Mirrored(), 0, Direction::Down));
}
}  // namespace

int main() {
    static_assert(!std::is_copy_constructible_v<DecodedSnapshot>);
    static_assert(std::is_nothrow_move_constructible_v<DecodedSnapshot>);
    static_assert(std::is_nothrow_move_assignable_v<DecodedSnapshot>);
    static_assert(sizeof(PenRange) <= 4U * sizeof(void*));

    DecodedSnapshot snapshot = MakeSnapshot();
    CheckSnapshotOwner(snapshot);
    const PenRange full = snapshot.Range();
    assert(!full.validated() && !snapshot.Validate());
    assert(!ValidatePenRange(full));
    assert(full.size() == 4U && !full.empty() && !full.mirrored());
    assert(full.front().position == 0 && full.back().position == 3);
    assert(full.global_position_at(0U) == 100);
    assert(full.global_position_at(3U) == 103);
    assert(full.snapshot_index_at(3U) == 3U);
    assert(PenIntrinsicLow(full[0U]) == -1);
    assert(PenIntrinsicHigh(full[0U]) == 10);
    assert(PenIntrinsicLow(full[2U]) == 3);
    assert(PenIntrinsicHigh(full[2U]) == 14);

    const PenRange middle = full.Subrange(1U, 4U).Prefix(2U);
    assert(middle.size() == 2U);
    assert(middle[0U].position == 0 && middle[1U].position == 1);
    assert(middle.global_position_at(0U) == 101);
    assert(middle.snapshot_index_at(1U) == 2U);
    assert(middle.front().start.bar == 1 && middle.back().end.bar == 3);
    assert(middle[0U].price_store == &snapshot.PriceBars());
    const PenRange last = middle.Subrange(1U, 2U);
    assert(last.front().position == 0 && last.global_position_at(0U) == 102);
    assert(last.front().start.bar == 2 && last.front().end.bar == 3);

    const PenRange mirror = middle.Mirrored();
    assert(mirror.mirrored());
    assert(mirror.global_position_at(0U) == middle.global_position_at(0U));
    assert(mirror[0U].start.bar == middle[0U].start.bar);
    assert(mirror[0U].start.price == -middle[0U].start.price);
    assert(mirror[0U].direction == Direction::Up);
    assert(mirror[0U].price_store == middle[0U].price_store);
    assert(mirror[0U].mirrored_prices);
    assert(PenIntrinsicLow(mirror[0U]) == -PenIntrinsicHigh(middle[0U]));
    assert(PenIntrinsicHigh(mirror[0U]) == -PenIntrinsicLow(middle[0U]));
    const PriceBar original_bar = middle[0U].price_bar_at(1);
    const PriceBar mirrored_bar = mirror[0U].price_bar_at(1);
    assert(mirrored_bar.bar == original_bar.bar);
    assert(mirrored_bar.low == -original_bar.high);
    assert(mirrored_bar.high == -original_bar.low);
    const PenRange restored = mirror.Mirrored();
    for (std::size_t index = 0; index < restored.size(); ++index) {
        CheckSamePen(restored[index], middle[index]);
    }
    CheckSamePen(mirror.Subrange(1U, 2U)[0U], last.Mirrored()[0U]);
    std::size_t visited = 0U;
    for (const Pen& pen : middle) {
        assert(pen.position == static_cast<std::int32_t>(visited));
        ++visited;
    }
    assert(visited == middle.size());
    const std::vector<Pen> materialized(mirror.begin(), mirror.end());
    assert(materialized.size() == mirror.size());
    CheckSamePen(materialized.back(), mirror.back());

    // Bounds remain tied to the visible prefix, even with a longer owner.
    ExpectOutOfRange([&] { static_cast<void>(middle.at(2U)); });
    ExpectOutOfRange([&] { static_cast<void>(middle.Prefix(3U)); });
    ExpectOutOfRange([&] { static_cast<void>(middle.Subrange(1U, 3U)); });
    ExpectOutOfRange([&] { static_cast<void>(middle.Subrange(2U, 1U)); });
    ExpectOutOfRange([&] { static_cast<void>(middle.global_position_at(2U)); });
    ExpectOutOfRange([&] { static_cast<void>(PenRange{snapshot.Pens(), 0U, 5U}); });
    ExpectOutOfRange([&] { static_cast<void>(middle.front().price_bar_at(0)); });
    ExpectOutOfRange([&] { static_cast<void>(middle.front().price_bar_at(3)); });
    assert(full.Prefix(0U).empty() && full.Subrange(4U, 4U).empty());
    const PenRange empty;
    assert(!empty.validated() && ValidatePenRange(empty));
    assert(empty.empty() && empty.begin() == empty.end());
    assert(empty.Prefix(0U).empty() && empty.Mirrored().Mirrored().empty());
    ExpectOutOfRange([&] { static_cast<void>(empty.front()); });
    ExpectOutOfRange([&] { static_cast<void>(empty.back()); });

    // Manual streams retain endpoint-only semantics and exact NaN fallback.
    std::vector<Pen> manual{ManualPen(7, 20, 10, 15)};
    assert(!manual[0U].has_price_path());
    assert(std::isnan(manual[0U].cached_path_high));
    assert(PenIntrinsicLow(manual[0U]) == 10 && PenIntrinsicHigh(manual[0U]) == 15);
    const PenRange manual_range{manual};
    const Pen mirrored_manual = manual_range.Mirrored()[0U];
    assert(!mirrored_manual.has_price_path());
    assert(PenIntrinsicLow(mirrored_manual) == -15);
    assert(PenIntrinsicHigh(mirrored_manual) == -10);
    manual[0U].price_store = &snapshot.PriceBars();
    manual[0U].start = Point{1, 8};
    manual[0U].end = Point{2, 4};
    manual[0U].direction = Direction::Down;
    assert(PenIntrinsicHigh(manual[0U]) == 10 && PenIntrinsicLow(manual[0U]) == 2);
    CachePenIntrinsicExtrema(manual[0U]);
    assert(manual[0U].cached_path_high == 10 && manual[0U].cached_path_low == 2);

    // A source Pen may already be mirrored; range mirroring composes by XOR.
    manual[0U] = PenRange{manual}.Mirrored()[0U];
    assert(manual[0U].mirrored_prices);
    const Pen unmirrored = PenRange{manual}.Mirrored()[0U];
    assert(!unmirrored.mirrored_prices && unmirrored.start.price == 8);
    assert(unmirrored.price_bar_at(1).high == 10);
    assert(PenIntrinsicHigh(unmirrored) == 10 && PenIntrinsicLow(unmirrored) == 2);

    // Moves rebind to the new vector object and leave all physical bars intact.
    DecodedSnapshot moved{std::move(snapshot)};
    CheckSnapshotOwner(moved);
    assert(moved.Pens()[0U].price_bar_at(0).low == -1);
    assert(moved.Pens()[3U].end.bar == 4);
    DecodedSnapshot assigned;
    assigned = std::move(moved);
    CheckSnapshotOwner(assigned);
    assert(assigned.Range().global_position_at(3U) == 103);
    DecodedSnapshot endpoint_only{{}, {ManualPen(0, 20, 10, 15)}};
    assert(!endpoint_only.Range().front().has_price_path());
    assert(PenIntrinsicLow(endpoint_only.Range().front()) == 10);
    assert(!endpoint_only.Range().validated() && endpoint_only.Validate());
    CheckManualValidation();
    CheckTrustedSnapshots();
    CheckInitialStructureFunctions();
    std::cout << "Snapshot ownership, bounded ranges, trust, validation, mirrors and extrema passed.\n";
}
