#include "segment_engine.hpp"

// Release 构建也必须执行测试断言，不能让 NDEBUG 把测试主体编译掉。
#if defined(NDEBUG)
#undef NDEBUG
#endif
#include <cassert>
#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

namespace {
using namespace tdx_segment;

void CheckDecoderTrust(const detail::DecodeResult& decoded) {
    assert(decoded.ok());
    assert(decoded.snapshot.Range().validated());
    // The raw-vector adapter has no trusted bit and independently walks every
    // invariant. This proves decoder provenance rather than testing its flag.
    const detail::PenRange borrowed{decoded.snapshot.Pens()};
    assert(!borrowed.validated());
    assert(detail::ValidatePenRange(borrowed));
    assert(detail::ValidatePenRange(borrowed.Mirrored()));
    assert(decoded.snapshot.Range().Mirrored().validated());
    for (const detail::Pen& pen : decoded.snapshot.Pens()) {
        assert(pen.price_store == &decoded.snapshot.PriceBars());
        double low = pen.low();
        double high = pen.high();
        for (std::int32_t bar = pen.start.bar; bar <= pen.end.bar; ++bar) {
            low = std::min(low, pen.price_bar_at(bar).low);
            high = std::max(high, pen.price_bar_at(bar).high);
        }
        assert(detail::PenIntrinsicLow(pen) == low);
        assert(detail::PenIntrinsicHigh(pen) == high);
    }
}

void CheckDecoderErrorPriority() {
    const float markers[] = {-1.0F, 1.0F, 0.0F, 0.0F};
    const float values[] = {10.0F, 9.0F, 8.0F, 7.0F};
    const auto expect = [](const TdxSeriesView& input, InputErrorCode code, std::int32_t bar) {
        const detail::DecodeResult decoded = detail::DecodePens(input);
        assert(!decoded.ok() && decoded.error == code && decoded.error_bar == bar);
        assert(!decoded.snapshot.Range().validated());
        assert(decoded.snapshot.Pens().empty());
    };
    expect(TdxSeriesView{4, markers, values, values}, InputErrorCode::InvalidPenPriceDirection, 1);
    // Raw validation finishes before checking Pen directions. Later raw errors
    // therefore take priority over the invalid first Pen, exactly as before.
    const float later_nonfinite[] = {10.0F, 9.0F, std::numeric_limits<float>::infinity(), 7.0F};
    expect(TdxSeriesView{4, markers, later_nonfinite, values}, InputErrorCode::NonFinitePrice, 2);
    const float later_high_below_low[] = {10.0F, 9.0F, 7.0F, 7.0F};
    expect(TdxSeriesView{4, markers, later_high_below_low, values}, InputErrorCode::HighBelowLow, 2);
    const float later_invalid_marker[] = {-1.0F, 1.0F, 0.5F, 0.0F};
    expect(TdxSeriesView{4, later_invalid_marker, values, values}, InputErrorCode::InvalidEndpointMarker, 2);
    expect(TdxSeriesView{4, later_invalid_marker, later_nonfinite, values}, InputErrorCode::NonFinitePrice, 2);
    expect(TdxSeriesView{4, later_invalid_marker, later_high_below_low, values}, InputErrorCode::HighBelowLow, 2);
    expect(TdxSeriesView{0, nullptr, nullptr, nullptr}, InputErrorCode::NonPositiveLength, -1);
    expect(TdxSeriesView{1, nullptr, values, values}, InputErrorCode::NullInputArray, -1);

    // Tail bars outside all completed Pen paths are still validated.
    const float good_markers[] = {-1.0F, 1.0F, 0.0F, 0.0F};
    const float good_values[] = {1.0F, 9.0F, 8.0F, 7.0F};
    const float bad_tail[] = {1.0F, 9.0F, 8.0F, std::numeric_limits<float>::quiet_NaN()};
    expect(TdxSeriesView{4, good_markers, bad_tail, good_values}, InputErrorCode::NonFinitePrice, 3);
}

void CheckPrefixProvenanceAndManualOwners() {
    // Includes no markers, one endpoint, replacement/ignored repeated kinds,
    // equal endpoint updates and bars visible after the last complete Pen.
    const float markers[] = {0, -1, -1, 0, 1, 1, 1, 0, -1, -1, 1, 0};
    const float highs[] = {6, 4, 3, 7, 10, 9, 10, 20, 6, 5, 12, 30};
    const float lows[] = {5, 3, 2, 6, 9, 8, 9, 1, 5, 4, 11, 0};
    float mirrored_markers[12];
    float mirrored_highs[12];
    float mirrored_lows[12];
    for (std::int32_t index = 0; index < 12; ++index) {
        mirrored_markers[index] = -markers[index];
        mirrored_highs[index] = -lows[index];
        mirrored_lows[index] = -highs[index];
    }
    for (std::int32_t length = 1; length <= 12; ++length) {
        CheckDecoderTrust(detail::DecodePens(TdxSeriesView{length, markers, highs, lows}));
        CheckDecoderTrust(detail::DecodePens(TdxSeriesView{
            length, mirrored_markers, mirrored_highs, mirrored_lows}));
    }

    detail::DecodeResult decoded = detail::DecodePens(TdxSeriesView{12, markers, highs, lows});
    std::vector<detail::PriceBar> prices = decoded.snapshot.PriceBars();
    std::vector<detail::Pen> pens = decoded.snapshot.Pens();
    prices[3U].bar = 2;
    detail::DecodedSnapshot invalid_manual{std::move(prices), std::move(pens)};
    assert(!invalid_manual.Range().validated());
    assert(!invalid_manual.Validate() && !invalid_manual.Range().validated());

    detail::DecodedSnapshot valid_manual{decoded.snapshot.PriceBars(), decoded.snapshot.Pens()};
    assert(!valid_manual.Range().validated());
    assert(valid_manual.Validate() && valid_manual.Range().validated());
    detail::DecodedSnapshot moved{std::move(decoded.snapshot)};
    assert(moved.Range().validated() && !decoded.snapshot.Range().validated());
    assert(detail::ValidatePenRange(detail::PenRange{moved.Pens()}));
}
}  // namespace

int main() {
    using namespace tdx_segment;

    // 分钟级 CLG3 的同类更新只需高于基础 CLG2，不保证逐次创新高：
    // 顶 10 -> 顶 12 -> 顶 11 中，11 是冗余候选，最终端点仍应为 12。
    const float markers[] = {-1.0F, 1.0F, 1.0F, 1.0F, -1.0F};
    const float highs[] = {2.0F, 10.0F, 12.0F, 11.0F, 8.0F};
    const float lows[] = {1.0F, 9.0F, 11.0F, 10.0F, 7.0F};

    const detail::DecodeResult decoded = detail::DecodePens(
        TdxSeriesView{5, markers, highs, lows}
    );
    assert(decoded.ok());
    assert(decoded.snapshot.Pens().size() == 2U);
    assert(decoded.snapshot.Pens()[0].start.bar == 0);
    assert(decoded.snapshot.Pens()[0].end.bar == 2);
    assert(decoded.snapshot.Pens()[0].end.price == 12.0);
    assert(decoded.snapshot.Pens()[1].start.bar == 2);
    assert(decoded.snapshot.Pens()[1].start.price == 12.0);
    assert(decoded.snapshot.Pens()[1].end.bar == 4);
    assert(decoded.snapshot.Pens()[1].end.price == 7.0);
    assert(decoded.snapshot.Pens()[0].price_store != nullptr);
    assert(decoded.snapshot.Pens()[0].price_store == decoded.snapshot.Pens()[1].price_store);
    assert(decoded.snapshot.Pens()[0].price_store->size() == 5U);
    assert(decoded.snapshot.Pens()[0].has_price_path());
    assert(decoded.snapshot.Pens()[1].has_price_path());
    assert(decoded.snapshot.Pens()[0].price_bar_at(1).high == 10.0);
    assert(decoded.snapshot.Pens()[1].price_bar_at(4).low == 7.0);

    detail::Pen mirrored = decoded.snapshot.Pens()[1];
    mirrored.mirrored_prices = true;
    assert(mirrored.price_store == decoded.snapshot.Pens()[1].price_store);
    assert(mirrored.price_bar_at(4).high == -7.0);
    assert(mirrored.price_bar_at(4).low == -8.0);

    // 同一极值稍后再次出现时，价格不变但端点时间应推进到最后一次
    // 更新；下一根反向笔从这个更晚的共享端点开始。
    const float equal_markers[] = {-1.0F, 1.0F, 1.0F, 1.0F, -1.0F};
    const float equal_highs[] = {2.0F, 10.0F, 12.0F, 12.0F, 8.0F};
    const float equal_lows[] = {1.0F, 9.0F, 11.0F, 11.0F, 7.0F};
    const detail::DecodeResult equal_extreme = detail::DecodePens(
        TdxSeriesView{5, equal_markers, equal_highs, equal_lows}
    );
    assert(equal_extreme.ok());
    assert(equal_extreme.snapshot.Pens().size() == 2U);
    assert(equal_extreme.snapshot.Pens()[0].end.bar == 3);
    assert(equal_extreme.snapshot.Pens()[0].end.price == 12.0);
    assert(equal_extreme.snapshot.Pens()[1].start.bar == 3);
    assert(equal_extreme.snapshot.Pens()[1].start.price == 12.0);

    CheckDecoderTrust(decoded);
    CheckDecoderTrust(equal_extreme);
    CheckDecoderErrorPriority();
    CheckPrefixProvenanceAndManualOwners();

    return 0;
}
