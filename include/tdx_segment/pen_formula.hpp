#pragma once

#include "tdx_segment.hpp"

namespace tdx_segment {

// Stateless preparation of the four-input legacy formula for the three-array ABI.
// 10: (C, DAY, C) -> bit0 MA(C,5)>MA(C,10), bit1 <, bit2 DAY changed,
//                       bit3 moving averages not yet valid, bit4 format marker.
// 11: (H, L, context) -> bit0 original CLG3SAFE, bit1 original CLD3SAFE.
// Keep both flags: drawing may use both; PENEVENT gives top precedence.
inline constexpr std::uint16_t kPenContextFunctionId = 10U;
inline constexpr std::uint16_t kPenFlagsFunctionId = 11U;

struct TdxPenContextView {
    std::int32_t data_len{};
    const float* closes{};
    const float* days{};
    const float* close_copy{};
};

struct TdxPenSeriesView {
    std::int32_t data_len{};
    const float* highs{};
    const float* lows{};
    const float* context{};
};

// Errors use the existing atomic convention: all zero, final value 64.
// Data belongs to the caller for this call only; no state survives a call.
[[nodiscard]] CalculationResult CalculatePenContextV1(const TdxPenContextView& input);
[[nodiscard]] CalculationResult CalculatePenFlagsV1(const TdxPenSeriesView& input);

}  // namespace tdx_segment
