#include "segment_engine.hpp"

#include <stdexcept>
#include <utility>

namespace tdx_segment::detail {
namespace {
[[noreturn]] void InvariantFailure(const char* message) {
    throw std::logic_error(message);
}

void Require(bool condition, const char* message) {
    if (!condition) {
        InvariantFailure(message);
    }
}

[[nodiscard]] bool HasStrictSegmentPriceDirection(
    Direction direction,
    const Point& start,
    const Point& endpoint
) noexcept {
    return direction == Direction::Up
        ? endpoint.price > start.price + kPriceEpsilon
        : endpoint.price < start.price - kPriceEpsilon;
}

[[nodiscard]] std::vector<SegmentCertificate> FlattenConfirmedSegmentsImpl(
    std::int32_t data_len,
    const std::vector<SegmentResult>& segments
) {
    std::vector<SegmentCertificate> certificates;
    certificates.reserve(segments.size());
    for (const SegmentResult& segment : segments) {
        Require(
            segment.status == SegmentStatus::Confirmed
                && segment.endpoint.has_value()
                && segment.confirmed_at.has_value(),
            "Only complete confirmed segments can be flattened."
        );
        Require(
            HasStrictSegmentPriceDirection(
                segment.direction,
                segment.start,
                *segment.endpoint
            ),
            "A segment with invalid net price direction cannot be flattened."
        );
        const bool in_range =
            segment.start.bar >= 0 && segment.start.bar < data_len
            && segment.endpoint->bar >= 0 && segment.endpoint->bar < data_len
            && segment.confirmed_at->bar >= 0
            && segment.confirmed_at->bar < data_len;
        Require(in_range, "Segment certificate bar is out of range.");
        certificates.push_back(SegmentCertificate{
            segment.direction,
            segment.start.bar,
            segment.endpoint->bar,
            segment.confirmed_at->bar,
        });
    }
    return certificates;
}

[[nodiscard]] std::vector<float> InputErrorMask(std::int32_t data_len) {
    if (data_len <= 0) {
        return {};
    }
    std::vector<float> output(static_cast<std::size_t>(data_len), 0.0F);
    output.back() = static_cast<float>(SegmentInputError);
    return output;
}

void SetEventBit(
    std::vector<float>& output,
    std::int32_t bar,
    std::uint16_t bit
) {
    const std::size_t index = static_cast<std::size_t>(bar);
    const auto current = static_cast<std::uint16_t>(output[index]);
    output[index] = static_cast<float>(current | bit);
}

[[nodiscard]] std::uint16_t EndpointBit(Direction direction) noexcept {
    return direction == Direction::Up
        ? UpSegmentEndpoint
        : DownSegmentEndpoint;
}

[[nodiscard]] std::uint16_t ConfirmationBit(Direction direction) noexcept {
    return direction == Direction::Up
        ? UpSegmentConfirmed
        : DownSegmentConfirmed;
}

[[nodiscard]] std::uint16_t ChainStartBit(Direction direction) noexcept {
    return direction == Direction::Up
        ? UpSegmentChainStart
        : DownSegmentChainStart;
}

[[nodiscard]] EventEncodingResult EncodeSegmentEventsImpl(
    std::int32_t data_len,
    const std::vector<SegmentCertificate>& certificates,
    bool input_error
) {
    if (data_len <= 0) {
        return EventEncodingResult{false, {}};
    }
    if (input_error) {
        return EventEncodingResult{false, InputErrorMask(data_len)};
    }

    std::vector<float> output(static_cast<std::size_t>(data_len), 0.0F);
    for (std::size_t index = 0; index < certificates.size(); ++index) {
        const SegmentCertificate& certificate = certificates[index];
        const bool fields_in_range =
            certificate.start_bar >= 0 && certificate.start_bar < data_len
            && certificate.endpoint_bar >= 0
            && certificate.endpoint_bar < data_len
            && certificate.confirmed_bar >= 0
            && certificate.confirmed_bar < data_len;
        const bool event_order_valid =
            certificate.start_bar < certificate.endpoint_bar
            && certificate.endpoint_bar <= certificate.confirmed_bar;
        if (!fields_in_range || !event_order_valid) {
            return EventEncodingResult{false, InputErrorMask(data_len)};
        }

        if (index > 0U) {
            const SegmentCertificate& previous = certificates[index - 1U];
            const bool chain_valid =
                certificate.direction != previous.direction
                && certificate.start_bar == previous.endpoint_bar
                && certificate.confirmed_bar >= previous.confirmed_bar;
            if (!chain_valid) {
                return EventEncodingResult{false, InputErrorMask(data_len)};
            }
        }

        if (index == 0U) {
            SetEventBit(
                output,
                certificate.start_bar,
                ChainStartBit(certificate.direction)
            );
        }
        SetEventBit(
            output,
            certificate.endpoint_bar,
            EndpointBit(certificate.direction)
        );
        SetEventBit(
            output,
            certificate.confirmed_bar,
            ConfirmationBit(certificate.direction)
        );
    }
    return EventEncodingResult{true, std::move(output)};
}


}  // namespace

std::vector<SegmentCertificate> FlattenConfirmedSegments(
    std::int32_t data_len,
    const std::vector<SegmentResult>& segments
) {
    return FlattenConfirmedSegmentsImpl(data_len, segments);
}

EventEncodingResult EncodeSegmentEvents(
    std::int32_t data_len,
    const std::vector<SegmentCertificate>& certificates,
    bool input_error
) {
    return EncodeSegmentEventsImpl(data_len, certificates, input_error);
}


}  // namespace tdx_segment::detail
