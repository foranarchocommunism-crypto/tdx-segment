// Exercise the production feature module at double precision through its seam.
#include "../src/feature_sequences.hpp"

#if defined(NDEBUG)
#undef NDEBUG
#endif
#include <cassert>
#include <iostream>

namespace {
using namespace tdx_segment::detail;

FeatureElement Raw(double low, double high, std::int32_t position) {
    return FeatureElement{low, high, {position, position}, position, position + 1};
}

Pen Reverse(double t, double low) {
    Pen pen;
    pen.start = Point{100, t};
    pen.end = Point{101, low};
    pen.direction = Direction::Down;
    return pen;
}

void CheckSelection(
    const PrimaryLeftPrefix& prefix, double t, double low,
    std::int32_t source, double expected_low, double expected_high,
    std::int32_t last_source = -1
) {
    const FeatureElement* a = prefix.CurrentA(t, Reverse(t, low));
    assert(a != nullptr);
    assert(a->source_pen_span.first_position == source);
    assert(a->source_pen_span.last_position == (last_source < 0 ? source : last_source));
    assert(a->low == expected_low && a->high == expected_high);
}

void CheckRange(const PrefixRangeMaximum& index, const std::vector<FeatureElement>& values) {
    for (std::size_t begin = 0; begin < values.size(); ++begin) {
        double maximum = -std::numeric_limits<double>::infinity();
        for (std::size_t end = begin + 1U; end <= values.size(); ++end) {
            maximum = std::max(maximum, values[end - 1U].high);
            assert(index.Query(begin, end) == maximum);
        }
    }
}
}  // namespace

int main() {
    PrimaryLeftPrefix single;
    assert(single.CurrentA(110, Reverse(110, 98)) == nullptr);
    single.Append(Raw(80, 100, 1));
    single.Append(Raw(75, 95, 3));
    CheckSelection(single, 110, 98, 1, 80, 100);  // author-shape special A=12
    CheckSelection(single, 110, 100, 1, 80, 100); // closed-interval contact
    CheckSelection(single, 110, 101, 3, 75, 95);  // no first-pen break: nearest
    CheckSelection(single, 100, 90, 3, 75, 95);  // T did not strictly exceed H
    CheckSelection(single, 100 + 0.5 * kPriceEpsilon, 90, 3, 75, 95);
    CheckSelection(single, 100 + 2 * kPriceEpsilon, 90, 1, 80, 100);
    CheckSelection(single, 110, 100 + 0.5 * kPriceEpsilon, 1, 80, 100);
    CheckSelection(single, 110, 100 + 2 * kPriceEpsilon, 3, 75, 95);
    single.Append(Raw(70, 90, 5));
    CheckSelection(single, 110, 98, 1, 80, 100);  // multiple strict lower tops

    PrimaryLeftPrefix higher_middle;
    higher_middle.Append(Raw(80, 100, 1));
    higher_middle.Append(Raw(85, 115, 3));
    higher_middle.Append(Raw(75, 95, 5));
    CheckSelection(higher_middle, 110, 98, 5, 75, 95);

    PrimaryLeftPrefix unavailable;
    unavailable.Append(Raw(2, 10, 1));
    unavailable.Append(Raw(4, 12, 3));
    assert(unavailable.CurrentA(11, Reverse(11, 3)) == nullptr);

    PrimaryLeftPrefix qualifying_boundary;
    qualifying_boundary.Append(Raw(2, 10 + 0.5 * kPriceEpsilon, 1));
    CheckSelection(qualifying_boundary, 10, 3, 1, 2, 10 + 0.5 * kPriceEpsilon);
    qualifying_boundary.Append(Raw(4, 10 + 2 * kPriceEpsilon, 3));
    assert(qualifying_boundary.CurrentA(10, Reverse(10, 3)) == nullptr);

    PrimaryLeftPrefix merged;
    merged.Append(Raw(0, 10, 1));
    merged.Append(Raw(2, 8, 3));
    merged.Append(Raw(1, 11, 5)); // merge then roll back into the left neighbor
    CheckSelection(merged, 12, 4, 1, 2, 11, 5);
    merged.Append(Raw(-1, 9, 7)); // reuse slots removed by containment
    CheckSelection(merged, 13, 5, 1, 2, 11, 5);
    CheckSelection(merged, 13, 12, 7, -1, 9);

    PrimaryLeftPrefix equal;
    equal.Append(Raw(80, 100, 1));
    equal.Append(Raw(75, 100, 3));
    CheckSelection(equal, 110, 98, 1, 80, 100, 3); // equal top merged; nearest

    PrimaryLeftPrefix tied;
    tied.Append(Raw(2, 12, 1));
    tied.Append(Raw(0, 10, 3));
    tied.Append(Raw(1, 12 - 0.75 * kPriceEpsilon, 5));
    tied.Append(Raw(-1, 9, 7));
    CheckSelection(tied, 13, 5, 5, 1, 12 - 0.75 * kPriceEpsilon);
    tied.Append(Raw(0, 12 - 1.5 * kPriceEpsilon, 9));
    CheckSelection(tied, 13, 5, 9, 0, 12 - 1.5 * kPriceEpsilon); // suffix not strictly lower

    PrefixRangeMaximum range;
    std::vector<FeatureElement> values;
    for (std::int32_t position = 0; position < 40; ++position) {
        values.push_back(Raw(-100, static_cast<double>((position * 17) % 41), position));
        range.SyncFrom(values, values.size() - 1U);
        CheckRange(range, values);
    }
    values.resize(3U);
    values[2].high = -5;
    range.SyncFrom(values, 2U);
    CheckRange(range, values);
    for (std::int32_t position = 3; position < 40; ++position) {
        values.push_back(Raw(-100, -static_cast<double>(position), position));
        range.SyncFrom(values, values.size() - 1U);
        CheckRange(range, values);
    }
    SecondaryHighestHighTracker secondary;
    std::vector<FeatureElement> secondary_values{
        Raw(2, 12, 1), Raw(0, 12 - 0.75 * kPriceEpsilon, 3),
        Raw(-2, 12 - 1.5 * kPriceEpsilon, 5),
    };
    secondary.SyncFrom(secondary_values, 0U);
    assert(!secondary.SelectBeforeB(0U).has_value());
    assert(secondary.SelectBeforeB(1U) == 0U);
    assert(secondary.SelectBeforeB(2U) == 1U); // latest EPS tie
    assert(secondary.SelectBeforeB(3U) == 1U); // original maximum is retained
    secondary_values.resize(2U);
    secondary_values[1U].high = 13;
    secondary.SyncFrom(secondary_values, 1U);
    assert(secondary.SelectBeforeB(2U) == 1U);
    secondary_values.push_back(Raw(-2, 13 - 0.5 * kPriceEpsilon, 5));
    secondary.SyncFrom(secondary_values, 2U);
    assert(secondary.SelectBeforeB(3U) == 2U);
    std::cout << "Primary A scope, EPS ties, containment sources and range rollback passed.\n";
}
