#include "tdx_segment/tdx_segment.hpp"
#include "tdx_segment/pen_formula.hpp"
#include "PluginTCalcFunc.h"

#include <algorithm>
#include <cstddef>
#include <type_traits>

namespace {

static_assert(sizeof(int) == 4U, "TongdaXin ABI requires a 32-bit int.");
static_assert(sizeof(float) == 4U, "TongdaXin ABI requires a 32-bit float.");
static_assert(
    std::is_same_v<decltype(PluginTCalcFuncInfo::pCallFunc), pPluginFUNC>,
    "PluginTCalcFuncInfo::pCallFunc must use pPluginFUNC."
);

void WriteAtomicError(
    int data_len,
    float* output,
    std::uint16_t error_bit
) noexcept {
    if (data_len <= 0 || output == nullptr) {
        return;
    }
    std::fill(output, output + data_len, 0.0F);
    output[data_len - 1] = static_cast<float>(error_bit);
}

template<class View>
void InvokeCalculation(
    tdx_segment::CalculationResult (*calculate)(const View&), std::uint16_t error_bit,
    int data_len, float* output, float* endpoint_markers, float* highs, float* lows
) noexcept {
    if (data_len <= 0 || output == nullptr) {
        return;
    }
    try {
        const View input{data_len, endpoint_markers, highs, lows};
        const auto result = calculate(input);
        if (result.event_mask.size() != static_cast<std::size_t>(data_len)) {
            WriteAtomicError(data_len, output, error_bit);
            return;
        }
        std::copy(result.event_mask.begin(), result.event_mask.end(), output);
    } catch (...) {
        // Complete the calculation before writing; exceptions never cross the C ABI.
        WriteAtomicError(data_len, output, error_bit);
    }
}

void CalcInterfaceProbeV1(
    int data_len, float* output, float* endpoint_markers, float* highs, float* lows
) noexcept {
    InvokeCalculation(tdx_segment::CalculateInterfaceProbeV1, tdx_segment::ProbeInputError,
        data_len, output, endpoint_markers, highs, lows);
}

void CalcSegmentEventsV1(
    int data_len, float* output, float* endpoint_markers, float* highs, float* lows
) noexcept {
    InvokeCalculation(tdx_segment::CalculateSegmentEventsV1, tdx_segment::SegmentInputError,
        data_len, output, endpoint_markers, highs, lows);
}

void CalcSegmentEventsV2(
    int data_len, float* output, float* endpoint_markers, float* highs, float* lows
) noexcept {
    InvokeCalculation(tdx_segment::CalculateSegmentEventsV2, tdx_segment::SegmentInputError,
        data_len, output, endpoint_markers, highs, lows);
}

void CalcPenContextV1(int length, float* output, float* closes, float* days, float* close_copy) noexcept {
    InvokeCalculation(tdx_segment::CalculatePenContextV1, tdx_segment::SegmentInputError,
        length, output, closes, days, close_copy);
}

void CalcPenFlagsV1(int length, float* output, float* highs, float* lows, float* context) noexcept {
    InvokeCalculation(tdx_segment::CalculatePenFlagsV1, tdx_segment::SegmentInputError,
        length, output, highs, lows, context);
}

}  // namespace

// 厂商规范要求所有回调都登记在这个全局数组中，并以 0 号项结束。
PluginTCalcFuncInfo g_CalcFuncSets[] = {
    {
        tdx_segment::kFormalSegmentFunctionId,
        &CalcSegmentEventsV1,
    },
    {
        tdx_segment::kRecoveryExperimentFunctionId,
        &CalcSegmentEventsV2,
    },
    {
        tdx_segment::kInterfaceProbeFunctionId,
        &CalcInterfaceProbeV1,
    },
    {tdx_segment::kPenContextFunctionId, &CalcPenContextV1},
    {tdx_segment::kPenFlagsFunctionId, &CalcPenFlagsV1},
    {0U, nullptr},
};

extern "C" BOOL RegisterTdxFunc(
    PluginTCalcFuncInfo** function_sets
) noexcept {
    if (function_sets == nullptr || *function_sets != nullptr) {
        return FALSE;
    }
    *function_sets = g_CalcFuncSets;
    return TRUE;
}
