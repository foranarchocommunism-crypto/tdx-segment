#pragma once

#include "segment_types.hpp"
#include "pen_snapshot.hpp"

namespace tdx_segment::detail {

struct DecodeResult {
    DecodedSnapshot snapshot;
    InputErrorCode error{InputErrorCode::None};
    std::int32_t error_bar{-1};

    [[nodiscard]] bool ok() const noexcept {
        return error == InputErrorCode::None;
    }
};

[[nodiscard]] DecodeResult DecodePens(const TdxSeriesView& input);

[[nodiscard]] std::optional<InitialSegmentStart> FindEarliestSegmentStart(
    const std::vector<Pen>& pens
);

[[nodiscard]] std::optional<InitialSegmentStart> FindEarliestSegmentStart(
    const DecodedSnapshot& snapshot
);

[[nodiscard]] AlternatingSegmentsResult JudgeAlternatingSegments(
    const DecodedSnapshot& snapshot,
    std::int32_t start_pos,
    Direction initial_direction,
    const LongStallPolicy& recovery_policy = DefaultLongStallPolicy()
);

[[nodiscard]] AlternatingSegmentsResult JudgeAlternatingSegments(
    const std::vector<Pen>& pens,
    std::int32_t start_pos,
    Direction initial_direction,
    const LongStallPolicy& recovery_policy = DefaultLongStallPolicy()
);

[[nodiscard]] std::vector<SegmentCertificate> FlattenConfirmedSegments(
    std::int32_t data_len,
    const std::vector<SegmentResult>& segments
);

[[nodiscard]] EventEncodingResult EncodeSegmentEvents(
    std::int32_t data_len,
    const std::vector<SegmentCertificate>& certificates,
    bool input_error = false
);

}  // namespace tdx_segment::detail
