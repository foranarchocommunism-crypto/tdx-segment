#include "segment_engine.hpp"
#include "feature_sequences.hpp"

#include <cmath>
#include <stdexcept>
#include <utility>

namespace tdx_segment::detail {
namespace {
enum class EndpointKind : std::int8_t {
    Bottom = -1,
    None = 0,
    Top = 1,
};

struct Endpoint {
    EndpointKind kind{EndpointKind::None};
    Point point{};
};

[[noreturn]] void InvariantFailure(const char* message) {
    throw std::logic_error(message);
}

void Require(bool condition, const char* message) {
    if (!condition) {
        InvariantFailure(message);
    }
}

[[nodiscard]] bool AlmostEqual(double left, double right) noexcept {
    return std::abs(left - right) <= kPriceEpsilon;
}

[[nodiscard]] bool TryDecodeEndpoint(
    float marker,
    EndpointKind& kind
) noexcept {
    if (!std::isfinite(marker)) {
        return false;
    }
    if (marker == -1.0F) {
        kind = EndpointKind::Bottom;
        return true;
    }
    if (marker == 0.0F) {
        kind = EndpointKind::None;
        return true;
    }
    if (marker == 1.0F) {
        kind = EndpointKind::Top;
        return true;
    }
    return false;
}

}  // namespace

DecodeResult DecodePens(const TdxSeriesView& input) {
    DecodeResult result;
    if (input.data_len <= 0) {
        result.error = InputErrorCode::NonPositiveLength;
        return result;
    }
    if (
        input.endpoint_markers == nullptr
        || input.highs == nullptr
        || input.lows == nullptr
    ) {
        result.error = InputErrorCode::NullInputArray;
        return result;
    }

    std::vector<PriceBar> all_price_bars;
    std::vector<Pen> decoded_pens;
    all_price_bars.reserve(static_cast<std::size_t>(input.data_len));
    std::vector<Endpoint> endpoints;
    endpoints.reserve(static_cast<std::size_t>(input.data_len));

    for (std::int32_t bar = 0; bar < input.data_len; ++bar) {
        const float high = input.highs[bar];
        const float low = input.lows[bar];
        if (!std::isfinite(high) || !std::isfinite(low)) {
            result.error = InputErrorCode::NonFinitePrice;
            result.error_bar = bar;
            return result;
        }
        if (high < low) {
            result.error = InputErrorCode::HighBelowLow;
            result.error_bar = bar;
            return result;
        }
        all_price_bars.push_back(PriceBar{
            bar,
            static_cast<double>(high),
            static_cast<double>(low),
        });

        EndpointKind kind{};
        if (!TryDecodeEndpoint(input.endpoint_markers[bar], kind)) {
            result.error = InputErrorCode::InvalidEndpointMarker;
            result.error_bar = bar;
            return result;
        }
        if (kind == EndpointKind::None) {
            continue;
        }
        Endpoint endpoint{
            kind,
            Point{
                bar,
                kind == EndpointKind::Top
                    ? static_cast<double>(high)
                    : static_cast<double>(low),
            },
        };
        if (!endpoints.empty() && endpoints.back().kind == kind) {
            const double previous_price = endpoints.back().point.price;
            const bool is_more_extreme = kind == EndpointKind::Top
                ? endpoint.point.price > previous_price + kPriceEpsilon
                : endpoint.point.price < previous_price - kPriceEpsilon;
            if (is_more_extreme || AlmostEqual(endpoint.point.price, previous_price)) {
                // 同价时采用更晚的端点更新；这与 CLG3/CLD3 的时间推进
                // 语义一致，并保持最终极值价格不变。
                endpoints.back() = endpoint;
            }
            // CLG3/CLD3 的每个同类信号只保证相对基础 CLG2/CLD2
            // 改善，并不保证相对上一次同类信号继续改善。较不极端的
            // 后续信号是冗余候选，忽略即可；不得据此否定整条笔流。
            continue;
        }
        endpoints.push_back(endpoint);
    }

    if (endpoints.size() >= 2U) {
        decoded_pens.reserve(endpoints.size() - 1U);
    }
    for (std::size_t index = 1; index < endpoints.size(); ++index) {
        const Endpoint& previous = endpoints[index - 1U];
        const Endpoint& current = endpoints[index];

        const Direction direction = previous.kind == EndpointKind::Bottom
            ? Direction::Up
            : Direction::Down;
        const bool valid_direction = direction == Direction::Up
            ? current.point.price
                > previous.point.price + kPriceEpsilon
            : current.point.price
                < previous.point.price - kPriceEpsilon;
        if (!valid_direction) {
            result.error = InputErrorCode::InvalidPenPriceDirection;
            result.error_bar = current.point.bar;
            decoded_pens.clear();
            return result;
        }

        Pen pen;
        pen.position = static_cast<std::int32_t>(decoded_pens.size());
        pen.start = previous.point;
        pen.end = current.point;
        pen.direction = direction;
        pen.price_store = &all_price_bars;
        decoded_pens.push_back(std::move(pen));
    }

    // Provenance is confined to this decoder: the first pass validates all raw
    // prices and markers, including bars after the last endpoint. It constructs
    // PriceBar{bar,...} in order. Endpoint folding retains increasing bars and
    // alternating kinds. The second pass checks every strict price direction,
    // numbers Pens in order and reuses each adjacent shared Point exactly.
    // Hence all ValidatePenRange invariants already hold. Ownership rebinding
    // and own-path extrema caching still run in the snapshot constructor.
    result.snapshot = DecodedSnapshot(
        std::move(all_price_bars),
        std::move(decoded_pens),
        DecodedSnapshot::DecoderValidatedInput{}
    );
    return result;
}

namespace {

[[nodiscard]] std::optional<InitialSegmentStart>
FindEarliestSegmentStartImpl(const PenRange& pens) {
    Require(
        ValidatePenRange(pens),
        "FindEarliestSegmentStart received an invalid pen stream."
    );
    for (
        std::int32_t start_pos = 0;
        start_pos + 2 < static_cast<std::int32_t>(pens.size());
        ++start_pos
    ) {
        const Direction direction =
            pens[static_cast<std::size_t>(start_pos)].direction;
        if (ThreePensMeetStrictInitialProgress(pens, start_pos, direction)) {
            return InitialSegmentStart{
                start_pos,
                direction,
                pens[static_cast<std::size_t>(start_pos)].start,
            };
        }
    }
    return std::nullopt;
}


}  // namespace

std::optional<InitialSegmentStart> FindEarliestSegmentStart(const std::vector<Pen>& pens) {
    return FindEarliestSegmentStartImpl(PenRange{pens});
}

std::optional<InitialSegmentStart> FindEarliestSegmentStart(const DecodedSnapshot& snapshot) {
    return FindEarliestSegmentStartImpl(snapshot.Range());
}

}  // namespace tdx_segment::detail
