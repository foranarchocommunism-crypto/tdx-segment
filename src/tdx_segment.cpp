#include "tdx_segment/tdx_segment.hpp"

#include "segment_engine.hpp"

#include <cstddef>
#include <stdexcept>
#include <utility>

namespace tdx_segment {
namespace {

CalculationResult MakeError(
    std::int32_t data_len,
    InputErrorCode error,
    std::int32_t error_bar,
    std::uint16_t output_bit
) {
    CalculationResult result;
    result.status = CalculationStatus::InvalidInput;
    result.error = error;
    result.error_bar = error_bar;
    if (data_len > 0) {
        result.event_mask.assign(static_cast<std::size_t>(data_len), 0.0F);
        result.event_mask.back() = static_cast<float>(output_bit);
    }
    return result;
}

CalculationResult MakeProbeError(
    std::int32_t data_len,
    InputErrorCode error,
    std::int32_t error_bar
) {
    CalculationResult result = MakeError(
        data_len,
        error,
        error_bar,
        ProbeInputError
    );
    if (result.event_mask.empty()) {
        return result;
    }

    const auto encoded_error = static_cast<std::uint16_t>(
        static_cast<std::uint16_t>(error) << kProbeErrorCodeShift
    );
    const auto last = static_cast<std::uint16_t>(result.event_mask.back());
    result.event_mask.back() = static_cast<float>(last | encoded_error);
    if (error_bar >= 0 && error_bar < data_len) {
        const auto index = static_cast<std::size_t>(error_bar);
        const auto current = static_cast<std::uint16_t>(
            result.event_mask[index]
        );
        result.event_mask[index] = static_cast<float>(
            current | ProbeErrorAtBar
        );
    }
    return result;
}

void SetBit(
    std::vector<float>& output,
    std::int32_t bar,
    std::uint16_t bit
) {
    const auto index = static_cast<std::size_t>(bar);
    const auto current = static_cast<std::uint16_t>(output[index]);
    output[index] = static_cast<float>(current | bit);
}

}  // namespace

CalculationResult CalculateInterfaceProbeV1(const TdxSeriesView& input) {
    const detail::DecodeResult decoded = detail::DecodePens(input);
    if (!decoded.ok()) {
        return MakeProbeError(
            input.data_len,
            decoded.error,
            decoded.error_bar
        );
    }

    CalculationResult result;
    result.event_mask.assign(
        static_cast<std::size_t>(input.data_len),
        0.0F
    );
    for (std::int32_t bar = 0; bar < input.data_len; ++bar) {
        if (input.endpoint_markers[bar] == 1.0F) {
            SetBit(result.event_mask, bar, ProbeTopEndpoint);
        } else if (input.endpoint_markers[bar] == -1.0F) {
            SetBit(result.event_mask, bar, ProbeBottomEndpoint);
        }
    }

    const auto initial = detail::FindEarliestSegmentStart(decoded.snapshot);
    if (initial.has_value()) {
        SetBit(
            result.event_mask,
            initial->start.bar,
            ProbeInitialSegmentStart
        );
        SetBit(
            result.event_mask,
            initial->start.bar,
            initial->direction == detail::Direction::Up
                ? ProbeInitialDirectionUp
                : ProbeInitialDirectionDown
        );
    }
    SetBit(
        result.event_mask,
        input.data_len - 1,
        ProbeCallSucceeded
    );
    return result;
}

static CalculationResult CalculateSegmentEventsImpl(
    const TdxSeriesView& input, const detail::LongStallPolicy& recovery_policy
) {
    const detail::DecodeResult decoded = detail::DecodePens(input);
    if (!decoded.ok()) {
        return MakeError(
            input.data_len,
            decoded.error,
            decoded.error_bar,
            SegmentInputError
        );
    }

    CalculationResult result;
    const auto initial = detail::FindEarliestSegmentStart(decoded.snapshot);
    if (!initial.has_value()) {
        result.event_mask.assign(
            static_cast<std::size_t>(input.data_len),
            0.0F
        );
        return result;
    }

    const detail::AlternatingSegmentsResult chain =
        detail::JudgeAlternatingSegments(
            decoded.snapshot,
            initial->start_pos,
            initial->direction,
            recovery_policy
        );
    if (
        chain.unresolved_segment.has_value()
        && chain.unresolved_segment->status
            == detail::SegmentStatus::InvalidInput
    ) {
        throw std::logic_error(
            "A pen stream produced by the TDX decoder became invalid in the engine."
        );
    }

    const auto certificates = detail::FlattenConfirmedSegments(
        input.data_len,
        chain.confirmed_segments
    );
    detail::EventEncodingResult encoded = detail::EncodeSegmentEvents(
        input.data_len,
        certificates
    );
    if (!encoded.valid) {
        throw std::logic_error(
            "The engine generated an invalid segment certificate chain."
        );
    }
    result.event_mask = std::move(encoded.event_mask);
    return result;
}

CalculationResult CalculateSegmentEventsV1(const TdxSeriesView& input) {
    return CalculateSegmentEventsImpl(input, detail::FormalRecoveryPolicy(true));
}

CalculationResult CalculateSegmentEventsV2(const TdxSeriesView& input) {
    return CalculateSegmentEventsImpl(input, detail::ExperimentalRecoveryPolicy(true));
}

CalculationResult CalculateSegmentEventsWithoutRecovery(const TdxSeriesView& input) {
    return CalculateSegmentEventsImpl(input, detail::RecoveryDisabledPolicy());
}

}  // namespace tdx_segment
