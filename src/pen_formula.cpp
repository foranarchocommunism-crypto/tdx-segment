#include "tdx_segment/pen_formula.hpp"
#include "formula_series.hpp"

#include <cmath>
#include <cstddef>
#include <utility>

namespace tdx_segment {
namespace {
CalculationResult Error(int length, InputErrorCode code, int bar = -1) {
    CalculationResult result;
    result.status = CalculationStatus::InvalidInput;
    result.error = code;
    result.error_bar = bar;
    if (length > 0) {
        result.event_mask.resize(static_cast<std::size_t>(length));
        result.event_mask.back() = static_cast<float>(SegmentInputError);
    }
    return result;
}
}  // namespace

CalculationResult CalculatePenContextV1(const TdxPenContextView& input) {
    using namespace pen_formula;
    const int length = input.data_len;
    if (length <= 0) { return Error(length, InputErrorCode::NonPositiveLength); }
    if (!input.closes || !input.days || !input.close_copy) { return Error(length, InputErrorCode::NullInputArray); }
    for (int i = 0; i < length; ++i) {
        if (!std::isfinite(input.closes[i]) || !std::isfinite(input.days[i]) || !std::isfinite(input.close_copy[i])) {
            return Error(length, InputErrorCode::NonFinitePrice, i);
        }
        if (input.days[i] < 1.0F || input.days[i] > 31.0F || std::floor(input.days[i]) != input.days[i] || input.close_copy[i] != input.closes[i]) {
            return Error(length, InputErrorCode::InvalidEndpointMarker, i);
        }
    }
    const auto size = static_cast<std::size_t>(length);
    const Series C(input.closes, size);
    const auto fast = MA(C, 5), slow = MA(C, 10);
    CalculationResult result;
    result.event_mask.resize(size);
    for (std::size_t i = 0; i < size; ++i) {
        const int trend = fast[i] > slow[i] ? 1 : (fast[i] < slow[i] ? 2 : 0);
        const int changed = i > 0 && input.days[i] != input.days[i - 1U] ? 4 : 0;
        const int warmup = !std::isfinite(fast[i]) || !std::isfinite(slow[i]) ? 8 : 0;
        result.event_mask[i] = static_cast<float>(16 + trend + changed + warmup);
    }
    return result;
}

CalculationResult CalculatePenFlagsV1(const TdxPenSeriesView& input) {
    using namespace pen_formula;
    const int length = input.data_len;
    if (length <= 0) { return Error(length, InputErrorCode::NonPositiveLength); }
    if (!input.highs || !input.lows || !input.context) { return Error(length, InputErrorCode::NullInputArray); }
    for (int i = 0; i < length; ++i) {
        if (!std::isfinite(input.highs[i]) || !std::isfinite(input.lows[i]) || !std::isfinite(input.context[i])) {
            return Error(length, InputErrorCode::NonFinitePrice, i);
        }
        if (input.highs[i] < input.lows[i]) { return Error(length, InputErrorCode::HighBelowLow, i); }
        const float context = input.context[i] - 16.0F;
        if (context < 0.0F || context > 15.0F || std::floor(context) != context || (static_cast<int>(context) & 3) == 3
            || ((static_cast<int>(context) & 8) != 0 && (static_cast<int>(context) & 3) != 0)) {
            return Error(length, InputErrorCode::InvalidEndpointMarker, i);
        }
    }
    const auto size = static_cast<std::size_t>(length);
    const Series H(input.highs, size), L(input.lows, size);
    Series CLG(size), CLD(size), day_change(size);
    for (std::size_t i = 0; i < size; ++i) {
        const int bits = static_cast<int>(input.context[i]) - 16;
        CLG.values[i] = (bits & 8) != 0 ? invalid : ((bits & 1) != 0 ? 1.0F : 0.0F);
        CLD.values[i] = (bits & 8) != 0 ? invalid : ((bits & 2) != 0 ? 1.0F : 0.0F);
        day_change.values[i] = (bits & 4) != 0 ? 1.0F : 0.0F;
    }
    // Literal port of tests/fixtures/legacy_pen_formula.txt; preserve rule order.
    // The final CLD1 comparison intentionally uses REF(H,...), as in the source.
    const auto CLHH = REF(H,5)==HHV(H,11);
    const auto CLLL = REF(L,5)==LLV(L,11);
    const auto CLFG01 = BACKSET(CLHH,6)>BACKSET(CLHH,5);
    const auto CLFD01 = BACKSET(CLLL,6)>BACKSET(CLLL,5);
    const auto CLFG02 = IF(BARSLAST(CLFG01)==BARSLAST(CLFD01) && CLG>CLD,CLFG01,IF(BARSLAST(CLFD01)>BARSLAST(CLFG01),CLFG01,0));
    const auto CLFD02 = IF(BARSLAST(CLFG01)==BARSLAST(CLFD01) && CLD>CLG,CLFD01,IF(BARSLAST(CLFG01)>BARSLAST(CLFD01),CLFD01,0));
    const auto CLFG0 = CLFG02 && H==HHV(H,BARSLAST(CLFD02));
    const auto CLFD0 = CLFD02 && L==LLV(L,BARSLAST(CLFG02));
    const auto CLGQ = L>REF(H,1) && day_change;
    const auto CLDQ = H<REF(L,1) && day_change;
    const auto CLFGH = REF(H,BARSLAST(CLFG0));
    const auto CLFGL = REF(L,BARSLAST(CLFG0));
    const auto CLFGH1 = REF(H,BARSLAST(CLFG0)+1);
    const auto CLFGL1 = REF(L,BARSLAST(CLFG0)+1);
    const auto CLFGH2 = REF(H,BARSLAST(CLFG0)+2);
    const auto CLFGL2 = REF(L,BARSLAST(CLFG0)+2);
    const auto CLFDH = REF(H,BARSLAST(CLFD0));
    const auto CLFDL = REF(L,BARSLAST(CLFD0));
    const auto CLFDH1 = REF(H,BARSLAST(CLFD0)+1);
    const auto CLFDL1 = REF(L,BARSLAST(CLFD0)+1);
    const auto CLFDH2 = REF(H,BARSLAST(CLFD0)+2);
    const auto CLFDL2 = REF(L,BARSLAST(CLFD0)+2);
    const auto CLFGZH = IF(CLFGL<=CLFGL1,CLFGH2,CLFGH1);
    const auto CLFGZL = IF(CLFGH1<CLFGL,CLFGL,IF(CLFGL<=CLFGL1,CLFGL2,CLFGL1));
    const auto CLFDZH = IF(CLFDL1>CLFDH,CLFDH,IF(CLFDH>=CLFDH1,CLFDH2,CLFDH1));
    const auto CLFDZL = IF(CLFDH>=CLFDH1,CLFDL2,CLFDL1);
    const auto CLFG1 = CLFGH>CLFDZH && CLFG0;
    const auto CLFD1 = CLFDL<CLFGZL && CLFD0;
    const auto CLFG = CLFG1 && CLFGH>CLFDH && CLFGL>CLFDL && CLFGL1>CLFDL;
    const auto CLFD = CLFD1 && CLFDL<CLFGL && CLFDH<CLFGH && CLFDH1<CLFGH;
    const auto CLBH0 = (H<=REF(H,1) && L>=REF(L,1)) || (H>=REF(H,1) && L<=REF(L,1));
    const auto CLBHG = COUNT(CLBH0,BARSLAST(CLFD0));
    const auto CLBHD = COUNT(CLBH0,BARSLAST(CLFG0));
    const auto CLBGQ = COUNT(CLGQ,BARSLAST(CLFD0));
    const auto CLBDQ = COUNT(CLDQ,BARSLAST(CLFG0));
    const auto CLBK0 = IF(CLBHG>0,CLBHG+2+1,IF(CLBHD,CLBHD+2+1,3));
    const auto CLBK = IF(CLBGQ,CLBK0-CLBGQ,IF(CLBDQ,CLBK0-CLBDQ,CLBK0));
    const auto CLG1X = (CLFG && BARSLAST(CLFD)>CLBK);
    const auto CLD1X = (CLFD && BARSLAST(CLFG)>CLBK);
    const auto CLG1 = (CLFG0 && REF(H,BARSLAST(CLFG0))>=REF(H,BARSLAST(CLG1X)) && BARSLAST(CLD1X)>BARSLAST(CLG1X)) || (CLFG1 && COUNT(CLGQ,BARSLAST(CLFD1))>0 && REF(H,BARSLAST(CLFG1))>REF(H,BARSLAST(CLG1X)));
    const auto CLD1 = (CLFD0 && REF(L,BARSLAST(CLFD0))<=REF(L,BARSLAST(CLD1X)) && BARSLAST(CLG1X)>BARSLAST(CLD1X)) || (CLFD1 && COUNT(CLDQ,BARSLAST(CLFG1))>0 && REF(L,BARSLAST(CLFD1))<REF(H,BARSLAST(CLD1X)));
    const auto CLG1H = IF(BARSLAST(CLD1)>BARSLAST(CLG1),REF(H,BARSLAST(CLG1)),REF(H,BARSLAST(CLD1)));
    const auto CLD1L = IF(BARSLAST(CLG1)>BARSLAST(CLD1),REF(L,BARSLAST(CLD1)),REF(L,BARSLAST(CLG1)));
    const auto CLG2 = CLG1 && H==HHV(CLG1H,BARSLAST(CLD1)+1) && H>REF(H,1) && BARSLAST(CLD1)>BARSLAST(CLG1);
    const auto CLD2 = CLD1 && L==LLV(CLD1L,BARSLAST(CLG1)+1) && L<REF(L,1) && BARSLAST(CLG1)>BARSLAST(CLD1);
    const auto CLG3 = CLG2 || (CLFG0 && REF(H,BARSLAST(CLFG0))>REF(H,BARSLAST(CLG2)) && BARSLAST(CLD2)>BARSLAST(CLG2));
    const auto CLD3 = CLD2 || (CLFD0 && REF(L,BARSLAST(CLFD0))<REF(L,BARSLAST(CLD2)) && BARSLAST(CLG2)>BARSLAST(CLD2));
    const auto CLG3SAFE = IF(ISVALID(CLG3),CLG3,0);
    const auto CLD3SAFE = IF(ISVALID(CLD3),CLD3,0);

    CalculationResult result;
    result.event_mask.resize(size);
    for (std::size_t i = 0; i < size; ++i) {
        result.event_mask[i] = (truth(CLG3SAFE[i]) ? 1.0F : 0.0F) + (truth(CLD3SAFE[i]) ? 2.0F : 0.0F);
    }
    return result;
}
}  // namespace tdx_segment
