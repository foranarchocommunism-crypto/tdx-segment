#include "tdx_segment/pen_formula.hpp"
#include "formula_series.hpp"

#include <array>
#include <cmath>
#include <iostream>
#include <limits>

int main() {
    using namespace tdx_segment;
    using namespace tdx_segment::pen_formula;
    const float prices[]{5, 3, 8, 2, 7};
    const Series source(prices, 5U);
    const float signal[]{0, 0, 1, 0, 0};
    const Series flags(signal, 5U);
    if (BARSLAST(flags).values != std::vector<float>{1,2,0,1,2}
        || BACKSET(flags, 2).values != std::vector<float>{0,1,1,0,0}
        || HHV(source, BARSLAST(flags)).values != std::vector<float>{5,5,8,2,7}
        || COUNT(flags, BARSLAST(flags)).values != std::vector<float>{0,0,1,0,0}
        || !std::isnan(REF(source, 5)[0]) || REF(source, 1)[4] != 2) {
        std::cerr << "Formula boundary primitives failed\n"; return 1;
    }
    std::array<float, 12> closes{}, days{}, high{}, low{};
    for (std::size_t i = 0; i < closes.size(); ++i) {
        closes[i] = static_cast<float>(i); high[i] = closes[i] + 1.0F;
        low[i] = closes[i] - 1.0F; days[i] = i < 10U ? 31.0F : 1.0F;
    }
    auto context = CalculatePenContextV1({12, closes.data(), days.data(), closes.data()});
    if (!context.ok() || context.event_mask[0] != 24 || context.event_mask[8] != 24
        || context.event_mask[9] != 17 || context.event_mask[10] != 21) { return 2; }
    auto result = CalculatePenFlagsV1({12, high.data(), low.data(), context.event_mask.data()});
    if (!result.ok()) { return 3; }
    auto is_error = [](const CalculationResult& r) {
        return !r.ok() && r.event_mask.size() == 12U && r.event_mask.back() == 64.0F
            && std::all_of(r.event_mask.begin(), r.event_mask.end()-1, [](float v) { return v == 0; });
    };
    days[3] = 0;
    if (!is_error(CalculatePenContextV1({12, closes.data(), days.data(), closes.data()}))) { return 4; }
    if (!is_error(CalculatePenFlagsV1({12, nullptr, low.data(), context.event_mask.data()}))) { return 5; }
    high[3] = std::numeric_limits<float>::infinity();
    if (!is_error(CalculatePenFlagsV1({12, high.data(), low.data(), context.event_mask.data()}))) { return 6; }
    high[3] = -100;
    if (!is_error(CalculatePenFlagsV1({12, high.data(), low.data(), context.event_mask.data()}))) { return 7; }
    high[3] = low[3];
    context.event_mask[3] = 64;
    if (!is_error(CalculatePenFlagsV1({12, high.data(), low.data(), context.event_mask.data()}))) { return 8; }
    context.event_mask[3] = 19;
    if (!is_error(CalculatePenFlagsV1({12, high.data(), low.data(), context.event_mask.data()}))) { return 9; }
    std::cout << "Pen primitives, context, month rollover, invalid inputs: passed\n";
    return 0;
}
