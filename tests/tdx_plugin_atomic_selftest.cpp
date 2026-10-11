#include "tdx_segment/tdx_segment.hpp"
#include "PluginTCalcFunc.h"

#include <array>
#include <cstddef>
#include <iostream>
#include <new>
#include <stdexcept>

extern "C" BOOL RegisterTdxFunc(PluginTCalcFuncInfo**) noexcept;

namespace {

enum class Mode { Success, ThrowLogic, ThrowAllocation, WrongLength };
Mode mode = Mode::Success;
std::size_t calculation_calls{};

tdx_segment::CalculationResult StubCalculation(const tdx_segment::TdxSeriesView& input) {
    ++calculation_calls;
    if (mode == Mode::ThrowLogic) { throw std::logic_error("deliberate adapter test"); }
    if (mode == Mode::ThrowAllocation) { throw std::bad_alloc{}; }
    tdx_segment::CalculationResult result;
    const std::size_t size = static_cast<std::size_t>(input.data_len);
    result.event_mask.resize(mode == Mode::WrongLength ? size - 1U : size);
    for (std::size_t i = 0; i < result.event_mask.size(); ++i) {
        // Read all input before the adapter commits, including when output aliases it.
        result.event_mask[i] = input.highs[i] + 1.0F;
    }
    return result;
}

bool Check(bool condition, const char* message) {
    if (!condition) { std::cerr << message << '\n'; }
    return condition;
}

}  // namespace

namespace tdx_segment {
CalculationResult CalculateInterfaceProbeV1(const TdxSeriesView& input) { return StubCalculation(input); }
CalculationResult CalculateSegmentEventsV1(const TdxSeriesView& input) { return StubCalculation(input); }
CalculationResult CalculateSegmentEventsV2(const TdxSeriesView& input) { return StubCalculation(input); }
}  // namespace tdx_segment

int main() {
    PluginTCalcFuncInfo* table = nullptr;
    if (!Check(RegisterTdxFunc(&table) == TRUE && table != nullptr, "Registration failed.")) { return 1; }
    for (std::size_t entry = 0; table[entry].nFuncMark != 0U; ++entry) {
        const auto callback = table[entry].pCallFunc;
        const float error_bit = static_cast<float>(
            table[entry].nFuncMark == tdx_segment::kInterfaceProbeFunctionId
                ? tdx_segment::ProbeInputError : tdx_segment::SegmentInputError);
        std::array<float, 4U> input{2.0F, 4.0F, 6.0F, 8.0F};
        for (const Mode test : {Mode::ThrowLogic, Mode::ThrowAllocation, Mode::WrongLength}) {
            mode = test;
            std::array<float, 6U> guarded{-17.0F, -9.0F, -9.0F, -9.0F, -9.0F, 19.0F};
            callback(4, guarded.data() + 1U, input.data(), input.data(), input.data());
            const std::array<float, 6U> expected{-17.0F, 0.0F, 0.0F, 0.0F, error_bit, 19.0F};
            if (!Check(guarded == expected, "Exception/size failure did not commit the exact atomic error.")) { return 2; }
        }
        const std::size_t before = calculation_calls;
        callback(4, nullptr, nullptr, nullptr, nullptr);
        callback(0, input.data(), nullptr, nullptr, nullptr);
        callback(-1, input.data(), nullptr, nullptr, nullptr);
        if (!Check(calculation_calls == before && input == std::array<float, 4U>{2,4,6,8},
                   "Invalid output/length should return before reading input or calling calculation.")) { return 3; }
        mode = Mode::Success;
        callback(4, input.data(), input.data(), input.data(), input.data());
        if (!Check(input == std::array<float, 4U>{3,5,7,9}, "Aliased output was written before calculation finished.")) { return 4; }
    }
    return 0;
}
