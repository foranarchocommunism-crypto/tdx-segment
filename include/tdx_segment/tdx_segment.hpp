#pragma once

#include <cstdint>
#include <vector>

namespace tdx_segment {

// 通达信传入的三条等长序列只在本次调用期间有效。实现不得缓存这些裸指针。
struct TdxSeriesView {
    std::int32_t data_len{};
    const float* endpoint_markers{};
    const float* highs{};
    const float* lows{};
};

enum class CalculationStatus : std::uint8_t {
    Ok = 0,
    InvalidInput,
};

enum class InputErrorCode : std::uint8_t {
    None = 0,
    NonPositiveLength,
    NullInputArray,
    NonFinitePrice,
    HighBelowLow,
    InvalidEndpointMarker,
    // 数值 6 曾用于“连续同类端点错误”。同类端点现在属于合法候选流，
    // 该诊断值永久停用；保留后续编号以免改变函数 99 的既有协议。
    InvalidPenPriceDirection = 7,
};

static_assert(
    static_cast<std::uint8_t>(InputErrorCode::InvalidPenPriceDirection) == 7U,
    "Function 99 diagnostic code 7 is part of the existing interface."
);

struct CalculationResult {
    CalculationStatus status{CalculationStatus::Ok};
    InputErrorCode error{InputErrorCode::None};
    std::int32_t error_bar{-1};
    std::vector<float> event_mask;

    [[nodiscard]] bool ok() const noexcept {
        return status == CalculationStatus::Ok;
    }
};

// 函数号 1 是正式的“笔事件/H/L -> 已确认线段事件”协议。
inline constexpr std::uint16_t kFormalSegmentFunctionId = 1U;
inline constexpr std::uint16_t kRecoveryExperimentFunctionId = 2U;

// 函数号 99 是独立的接线自检协议，不是线段判断结果。
inline constexpr std::uint16_t kInterfaceProbeFunctionId = 99U;

enum InterfaceProbeBit : std::uint16_t {
    ProbeTopEndpoint = 1U << 0U,
    ProbeBottomEndpoint = 1U << 1U,
    ProbeInitialSegmentStart = 1U << 2U,
    ProbeInitialDirectionUp = 1U << 3U,
    ProbeInitialDirectionDown = 1U << 4U,
    ProbeCallSucceeded = 1U << 5U,
    ProbeInputError = 1U << 6U,
    ProbeErrorAtBar = 1U << 11U,
};

inline constexpr std::uint16_t kProbeErrorCodeShift = 7U;
inline constexpr std::uint16_t kProbeErrorCodeMask = 0x0780U;

// 函数号 1 的正式逐 K 线事件协议。
enum SegmentEventBit : std::uint16_t {
    UpSegmentEndpoint = 1U << 0U,
    DownSegmentEndpoint = 1U << 1U,
    UpSegmentConfirmed = 1U << 2U,
    DownSegmentConfirmed = 1U << 3U,
    UpSegmentChainStart = 1U << 4U,
    DownSegmentChainStart = 1U << 5U,
    SegmentInputError = 1U << 6U,
};

// 深模块的当前可执行入口：一次调用完成三数组校验、笔恢复、最早初始线段
// 三笔窗口搜索和诊断事件编码。它只用于验证 ABI 与输入 seam，不输出线段。
//
// 成功时：
//   - 顶/底端点分别回显 bit 0/bit 1；
//   - 若存在最早初始三笔窗口，在其起点写 bit 2，并写方向 bit 3/bit 4；
//   - 最后一根 K 线写 bit 5，证明 DLL 函数确实被执行。
// 失败时：清空全部部分结果，只在最后一根 K 线写 bit 6。
[[nodiscard]] CalculationResult CalculateInterfaceProbeV1(
    const TdxSeriesView& input
);

// 正式线段深入口：一次完整快照完成笔恢复、初始锚点搜索、上下镜像、
// 走势 A 嵌套、交替线段链和事件编码。合法但尚未形成或确认线段时返回
// Ok 与全零事件；输入不合法时仅在最后一根 K 线写 bit 6。
[[nodiscard]] CalculationResult CalculateSegmentEventsV1(
    const TdxSeriesView& input
);

[[nodiscard]] CalculationResult CalculateSegmentEventsV2(const TdxSeriesView& input);

// C++ baseline seam for validation; this is not registered as a DLL function.
[[nodiscard]] CalculationResult CalculateSegmentEventsWithoutRecovery(const TdxSeriesView& input);

}  // namespace tdx_segment
