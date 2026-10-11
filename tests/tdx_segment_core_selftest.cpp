#include "tdx_segment/tdx_segment.hpp"

// Release 构建也必须执行测试断言，不能让 NDEBUG 把测试主体编译掉。
#if defined(NDEBUG)
#undef NDEBUG
#endif
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <future>
#include <limits>
#include <vector>

namespace {

bool HasBit(float encoded, std::uint16_t bit) {
    const auto value = static_cast<std::uint16_t>(encoded);
    return (value & bit) != 0U;
}

}  // namespace

int main() {
    using namespace tdx_segment;

    const float markers[] = {-1.0F, 0.0F, 1.0F, 0.0F, -1.0F, 1.0F};
    const float highs[] = {1.0F, 2.0F, 5.0F, 4.0F, 3.0F, 8.0F};
    const float lows[] = {0.0F, 1.0F, 4.0F, 3.0F, 1.0F, 7.0F};
    const TdxSeriesView input{6, markers, highs, lows};

    const CalculationResult result = CalculateInterfaceProbeV1(input);
    assert(result.ok());
    assert(result.error == InputErrorCode::None);
    assert(result.event_mask.size() == 6U);
    assert(HasBit(result.event_mask[0], ProbeBottomEndpoint));
    assert(HasBit(result.event_mask[0], ProbeInitialSegmentStart));
    assert(HasBit(result.event_mask[0], ProbeInitialDirectionUp));
    assert(HasBit(result.event_mask[2], ProbeTopEndpoint));
    assert(HasBit(result.event_mask[4], ProbeBottomEndpoint));
    assert(HasBit(result.event_mask[5], ProbeTopEndpoint));
    assert(HasBit(result.event_mask[5], ProbeCallSucceeded));
    assert(!HasBit(result.event_mask[5], ProbeInputError));

    // 首个向上三笔有重叠但第三笔未创新高，自动初始锚点须后移。
    // bar=1 的向下三笔同时顶降低、底创新低，因此从该处启动。
    const float shifted_markers[] = {-1.0F, 1.0F, -1.0F, 1.0F, -1.0F};
    const float shifted_highs[] = {0.2F, 5.0F, 4.2F, 4.5F, 2.2F};
    const float shifted_lows[] = {0.0F, 4.8F, 4.0F, 4.3F, 2.0F};
    const CalculationResult shifted = CalculateInterfaceProbeV1(
        TdxSeriesView{5, shifted_markers, shifted_highs, shifted_lows}
    );
    assert(shifted.ok());
    assert(!HasBit(shifted.event_mask[0], ProbeInitialSegmentStart));
    assert(HasBit(shifted.event_mask[1], ProbeInitialSegmentStart));
    assert(HasBit(shifted.event_mask[1], ProbeInitialDirectionDown));
    assert(!HasBit(shifted.event_mask[1], ProbeInitialDirectionUp));

    // 第二笔恰好回到起点也不合格；向上和向下都跳到 bar=2。
    for (int mirror = 0; mirror < 2; ++mirror) {
        float equal_markers[] = {-1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F};
        float equal_prices[] = {0.0F, 5.0F, 0.0F, 8.0F, 4.0F, 10.0F};
        if (mirror != 0) {
            for (int i = 0; i < 6; ++i) {
                equal_markers[i] = -equal_markers[i];
                equal_prices[i] = 10.0F - equal_prices[i];
            }
        }
        const auto equal_seed = CalculateInterfaceProbeV1(
            TdxSeriesView{6, equal_markers, equal_prices, equal_prices}
        );
        assert(equal_seed.ok());
        assert(!HasBit(equal_seed.event_mask[0], ProbeInitialSegmentStart));
        assert(HasBit(equal_seed.event_mask[2], ProbeInitialSegmentStart));
        assert(HasBit(equal_seed.event_mask[2], mirror == 0
            ? ProbeInitialDirectionUp : ProbeInitialDirectionDown));
    }

    // CZSC 同价端点反例：B 合并后选择最后同价顶/底（BASIS-030）。
    for (int mirror = 0; mirror < 2; ++mirror) {
        float tie_markers[] = {-1.0F, 1.0F, 0.0F, 0.0F, 0.0F, -1.0F, 1.0F, -1.0F, 0.0F, 0.0F, 0.0F, 1.0F, -1.0F, 0.0F, 0.0F, 0.0F, 1.0F, -1.0F, 0.0F};
        float tie_highs[] = {4050.191650390625F, 4067.83203125F, 4067.12646484375F, 4066.420654296875F, 4065.715087890625F, 4065.009521484375F, 4097.46728515625F, 4066.420654296875F, 4081.238525390625F, 4086.88330078125F, 4091.82275390625F, 4097.46728515625F, 4022.673095703125F, 4024.084228515625F, 4025.495361328125F, 4026.90673828125F, 4029.0234375F, 4013.500244140625F, 4016.050048828125F};
        float tie_lows[] = {4048.074951171875F, 4066.420654296875F, 4065.715087890625F, 4065.009521484375F, 4064.303955078125F, 4063.598388671875F, 4093.23388671875F, 4060.0703125F, 4075.59375F, 4080.532958984375F, 4085.47216796875F, 4091.116943359375F, 4019.8505859375F, 4021.26171875F, 4022.673095703125F, 4024.789794921875F, 4026.201171875F, 4010.677734375F, 4012.79443359375F};
        if (mirror != 0) {
            for (int i = 0; i < 19; ++i) {
                tie_markers[i] = -tie_markers[i];
                const float old_high = tie_highs[i];
                tie_highs[i] = 8192.0F - tie_lows[i];
                tie_lows[i] = 8192.0F - old_high;
            }
        }
        const auto tie_result = CalculateSegmentEventsV1(
            TdxSeriesView{19, tie_markers, tie_highs, tie_lows}
        );
        assert(tie_result.ok());
        const auto endpoint_bit = mirror == 0 ? UpSegmentEndpoint : DownSegmentEndpoint;
        assert(!HasBit(tie_result.event_mask[6], endpoint_bit));
        assert(HasBit(tie_result.event_mask[11], endpoint_bit));
    }

    const float no_initial_markers[] = {-1.0F, 1.0F, -1.0F, 1.0F};
    const float no_initial_prices[] = {10.0F, 20.0F, 1.0F, 2.0F};
    const CalculationResult no_initial = CalculateInterfaceProbeV1(
        TdxSeriesView{
            4,
            no_initial_markers,
            no_initial_prices,
            no_initial_prices,
        }
    );
    assert(no_initial.ok());
    assert(HasBit(no_initial.event_mask[3], ProbeCallSucceeded));
    assert(!HasBit(no_initial.event_mask[0], ProbeInitialSegmentStart));

    // 只有一个端点或尚无合格初始三笔属于正常待定，不是输入错误。
    const float short_markers[] = {-1.0F, 0.0F, 0.0F};
    const float short_highs[] = {2.0F, 3.0F, 4.0F};
    const float short_lows[] = {1.0F, 2.0F, 3.0F};
    const CalculationResult short_result = CalculateInterfaceProbeV1(
        TdxSeriesView{3, short_markers, short_highs, short_lows}
    );
    assert(short_result.ok());
    assert(HasBit(short_result.event_mask[0], ProbeBottomEndpoint));
    assert(HasBit(short_result.event_mask[2], ProbeCallSucceeded));
    assert(!HasBit(short_result.event_mask[2], ProbeInputError));

    const float bad_markers[] = {-1.0F, 0.5F, 1.0F};
    const CalculationResult bad_marker = CalculateInterfaceProbeV1(
        TdxSeriesView{3, bad_markers, short_highs, short_lows}
    );
    assert(!bad_marker.ok());
    assert(bad_marker.error == InputErrorCode::InvalidEndpointMarker);
    assert(bad_marker.error_bar == 1);
    assert(bad_marker.event_mask[0] == 0.0F);
    assert(bad_marker.event_mask[1] == static_cast<float>(ProbeErrorAtBar));
    assert(
        bad_marker.event_mask[2]
        == static_cast<float>(
            ProbeInputError
            | (
                static_cast<std::uint16_t>(
                    InputErrorCode::InvalidEndpointMarker
                )
                << kProbeErrorCodeShift
            )
        )
    );

    // CLG3/CLD3 会在反向端点出现前继续发出同类端点延伸：连续顶必须
    // 保留更高者，连续底必须保留更低者，再恢复最终交替笔流。
    const float extension_markers[] = {
        -1.0F, 1.0F, 1.0F, -1.0F, -1.0F, 1.0F,
    };
    const float extension_highs[] = {
        2.0F, 5.0F, 6.0F, 4.0F, 3.0F, 7.0F,
    };
    const float extension_lows[] = {
        1.0F, 4.0F, 5.0F, 3.0F, 2.0F, 6.0F,
    };
    const CalculationResult extensions = CalculateInterfaceProbeV1(
        TdxSeriesView{
            6,
            extension_markers,
            extension_highs,
            extension_lows,
        }
    );
    assert(extensions.ok());
    assert(HasBit(extensions.event_mask[2], ProbeTopEndpoint));
    assert(HasBit(extensions.event_mask[4], ProbeBottomEndpoint));
    assert(HasBit(extensions.event_mask[5], ProbeCallSucceeded));

    const float non_improving_markers[] = {-1.0F, 1.0F, 1.0F};
    const float non_improving_highs[] = {2.0F, 6.0F, 5.0F};
    const float non_improving_lows[] = {1.0F, 5.0F, 4.0F};
    const CalculationResult non_improving = CalculateInterfaceProbeV1(
        TdxSeriesView{
            3,
            non_improving_markers,
            non_improving_highs,
            non_improving_lows,
        }
    );
    assert(non_improving.ok());
    assert(HasBit(non_improving.event_mask[2], ProbeTopEndpoint));
    assert(HasBit(non_improving.event_mask[2], ProbeCallSucceeded));

    const float wrong_direction_markers[] = {-1.0F, 1.0F};
    const float wrong_direction_highs[] = {6.0F, 4.0F};
    const float wrong_direction_lows[] = {5.0F, 3.0F};
    const CalculationResult wrong_direction = CalculateInterfaceProbeV1(
        TdxSeriesView{
            2,
            wrong_direction_markers,
            wrong_direction_highs,
            wrong_direction_lows,
        }
    );
    assert(!wrong_direction.ok());
    assert(
        wrong_direction.error == InputErrorCode::InvalidPenPriceDirection
    );

    const float nan_highs[] = {
        1.0F,
        std::numeric_limits<float>::quiet_NaN(),
        3.0F,
    };
    const CalculationResult non_finite = CalculateInterfaceProbeV1(
        TdxSeriesView{3, short_markers, nan_highs, short_lows}
    );
    assert(!non_finite.ok());
    assert(non_finite.error == InputErrorCode::NonFinitePrice);
    assert(non_finite.error_bar == 1);

    const float inverted_highs[] = {2.0F, 1.0F, 4.0F};
    const CalculationResult inverted = CalculateInterfaceProbeV1(
        TdxSeriesView{3, short_markers, inverted_highs, short_lows}
    );
    assert(!inverted.ok());
    assert(inverted.error == InputErrorCode::HighBelowLow);
    assert(inverted.error_bar == 1);

    const CalculationResult null_input = CalculateInterfaceProbeV1(
        TdxSeriesView{3, nullptr, nullptr, nullptr}
    );
    assert(!null_input.ok());
    assert(null_input.error == InputErrorCode::NullInputArray);
    assert(
        null_input.event_mask.back()
        == static_cast<float>(
            ProbeInputError
            | (
                static_cast<std::uint16_t>(InputErrorCode::NullInputArray)
                << kProbeErrorCodeShift
            )
        )
    );

    const CalculationResult empty = CalculateInterfaceProbeV1(
        TdxSeriesView{0, nullptr, nullptr, nullptr}
    );
    assert(!empty.ok());
    assert(empty.error == InputErrorCode::NonPositiveLength);
    assert(empty.event_mask.empty());

    const float formal_markers[] = {
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F, -1.0F,
    };
    const float formal_highs[] = {
        0.0F, 5.0F, 2.0F, 8.0F, 4.0F, 6.0F, 1.0F,
    };
    const float formal_lows[] = {
        0.0F, 5.0F, 2.0F, 8.0F, 4.0F, 6.0F, 1.0F,
    };
    const TdxSeriesView formal_input{
        7,
        formal_markers,
        formal_highs,
        formal_lows,
    };
    const CalculationResult formal_result = CalculateSegmentEventsV1(
        formal_input
    );
    assert(formal_result.ok());
    assert(formal_result.event_mask.size() == 7U);
    assert(
        formal_result.event_mask[0]
        == static_cast<float>(UpSegmentChainStart)
    );
    assert(
        formal_result.event_mask[3]
        == static_cast<float>(UpSegmentEndpoint)
    );
    assert(
        formal_result.event_mask[6]
        == static_cast<float>(UpSegmentConfirmed)
    );

    // T=9 被第一根原始创新高笔否定后记录 T'=13；时间上位于两者
    // 之间的候选 5 仍须接受完整判断，抵达 T'=13 后再由第一种情况
    // 确认。旧的跳转逻辑会越过中间候选，因而这个外部事件结果能够
    // 直接区分“逐候选检查”和“跳到 T'”。
    const float intermediate_markers[] = {
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F,
    };
    const float intermediate_prices[] = {
        0.0F, 3.0F, 2.0F, 9.0F, 4.0F, 5.0F,
        1.0F, 13.0F, 3.0F, 12.0F, 2.0F,
    };
    const CalculationResult intermediate_result = CalculateSegmentEventsV1(
        TdxSeriesView{
            11,
            intermediate_markers,
            intermediate_prices,
            intermediate_prices,
        }
    );
    assert(intermediate_result.ok());
    assert(
        intermediate_result.event_mask[0]
        == static_cast<float>(UpSegmentChainStart)
    );
    assert(
        intermediate_result.event_mask[7]
        == static_cast<float>(UpSegmentEndpoint)
    );
    assert(
        intermediate_result.event_mask[10]
        == static_cast<float>(UpSegmentConfirmed)
    );
    assert(intermediate_result.event_mask[9] == 0.0F);

    // 候选 T=17 左侧包含标准化后有两个向下元素 [10,20]、[3,12]。
    // 普通路径直接取最近 [3,12]；更早的 [10,20] 不影响它，不跳过 T。
    // 本短快照尚无终局证据，所以事件仍全零。
    const float highest_left_a_markers[] = {
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F,
        1.0F, -1.0F, 1.0F, -1.0F,
    };
    const float highest_left_a_prices[] = {
        11.0F, 20.0F, 10.0F, 12.0F, 3.0F,
        17.0F, 12.0F, 16.0F, 3.0F,
    };
    const CalculationResult highest_left_a_result = CalculateSegmentEventsV1(
        TdxSeriesView{
            9,
            highest_left_a_markers,
            highest_left_a_prices,
            highest_left_a_prices,
        }
    );
    assert(highest_left_a_result.ok());
    assert(std::all_of(
        highest_left_a_result.event_mask.begin(),
        highest_left_a_result.event_mask.end(),
        [](float value) { return value == 0.0F; }
    ));

    const float mirrored_highest_left_a_markers[] = {
        1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
        -1.0F, 1.0F, -1.0F, 1.0F,
    };
    const float mirrored_highest_left_a_prices[] = {
        -11.0F, -20.0F, -10.0F, -12.0F, -3.0F,
        -17.0F, -12.0F, -16.0F, -3.0F,
    };
    const CalculationResult mirrored_highest_left_a_result =
        CalculateSegmentEventsV1(
            TdxSeriesView{
                9,
                mirrored_highest_left_a_markers,
                mirrored_highest_left_a_prices,
                mirrored_highest_left_a_prices,
            }
        );
    assert(mirrored_highest_left_a_result.ok());
    assert(std::all_of(
        mirrored_highest_left_a_result.event_mask.begin(),
        mirrored_highest_left_a_result.event_mask.end(),
        [](float value) { return value == 0.0F; }
    ));

    // 普通最近元素合格时，更早高于 T 的元素不影响连续链。
    // 范围收窄后仍保留第三段 bar=15；镜像方向相同。
    for (int mirror = 0; mirror < 2; ++mirror) {
        float a_above_t_markers[] = {
            -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
            -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
            -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
            -1.0F, 1.0F,
        };
        float a_above_t_prices[] = {
            0.0F, 10.0F, 5.0F, 12.0F, 0.0F, 19.0F,
            10.0F, 17.0F, 0.0F, 18.0F, 3.0F, 5.0F,
            4.0F, 7.0F, 2.0F, 8.0F, 4.0F, 5.0F,
            0.0F, 4.0F,
        };
        if (mirror != 0) {
            for (int i = 0; i < 20; ++i) {
                a_above_t_markers[i] = -a_above_t_markers[i];
                a_above_t_prices[i] = -a_above_t_prices[i];
            }
        }
        const CalculationResult a_above_t_result = CalculateSegmentEventsV1(
            TdxSeriesView{
                20,
                a_above_t_markers,
                a_above_t_prices,
                a_above_t_prices,
            }
        );
        assert(a_above_t_result.ok());
        assert(HasBit(
            a_above_t_result.event_mask[15],
            mirror == 0 ? UpSegmentEndpoint : DownSegmentEndpoint
        ));
    }

    const float mirrored_formal_markers[] = {
        1.0F, -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
    };
    const float mirrored_formal_highs[] = {
        0.0F, -5.0F, -2.0F, -8.0F, -4.0F, -6.0F, -1.0F,
    };
    const float mirrored_formal_lows[] = {
        0.0F, -5.0F, -2.0F, -8.0F, -4.0F, -6.0F, -1.0F,
    };
    const TdxSeriesView mirrored_formal_input{
        7,
        mirrored_formal_markers,
        mirrored_formal_highs,
        mirrored_formal_lows,
    };
    const CalculationResult mirrored_formal_result =
        CalculateSegmentEventsV1(mirrored_formal_input);
    assert(mirrored_formal_result.ok());
    assert(
        mirrored_formal_result.event_mask[0]
        == static_cast<float>(DownSegmentChainStart)
    );
    assert(
        mirrored_formal_result.event_mask[3]
        == static_cast<float>(DownSegmentEndpoint)
    );
    assert(
        mirrored_formal_result.event_mask[6]
        == static_cast<float>(DownSegmentConfirmed)
    );

    // 语义 UP 的已确认端点必须严格高于线段起点。旧实现会让没有
    // 标准 A-B-C 的 T=9 通过走势 A 成为从 10 开始的 UP 端点。
    const float wrong_net_first_markers[] = {
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F,
        1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F,
    };
    const float wrong_net_first_prices[] = {
        10.0F, 15.0F, 12.0F, 13.0F, 11.0F,
        14.0F, 5.0F, 6.0F, 2.0F, 9.0F,
        3.0F, 7.0F, 4.0F, 8.0F, 1.0F,
    };
    const CalculationResult wrong_net_first = CalculateSegmentEventsV1(
        TdxSeriesView{
            15,
            wrong_net_first_markers,
            wrong_net_first_prices,
            wrong_net_first_prices,
        }
    );
    assert(wrong_net_first.ok());
    assert(!HasBit(wrong_net_first.event_mask[9], UpSegmentEndpoint));

    // 同一漏洞也能发生在后续段：先合法确认 UP 1->9，旧实现随后
    // 输出语义 DOWN 9->14。修复必须保留第一段，同时拒绝后续错误端点。
    const float wrong_net_later_markers[] = {
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F,
        -1.0F, 1.0F, -1.0F, 1.0F,
    };
    const float wrong_net_later_prices[] = {
        1.0F, 8.0F, 7.0F, 9.0F, 2.0F, 6.0F,
        4.0F, 5.0F, 3.0F, 11.0F, 10.0F, 15.0F,
        12.0F, 16.0F, 13.0F, 19.0F, 14.0F, 21.0F,
        18.0F, 20.0F, 17.0F, 22.0F,
    };
    const CalculationResult wrong_net_later = CalculateSegmentEventsV1(
        TdxSeriesView{
            22,
            wrong_net_later_markers,
            wrong_net_later_prices,
            wrong_net_later_prices,
        }
    );
    assert(wrong_net_later.ok());
    assert(HasBit(wrong_net_later.event_mask[3], UpSegmentEndpoint));
    assert(!HasBit(wrong_net_later.event_mask[16], DownSegmentEndpoint));

    // 相同快照重复调用以及并发调用必须逐位一致，不能依赖可变全局状态。
    std::vector<std::future<std::vector<float>>> tasks;
    for (int index = 0; index < 16; ++index) {
        tasks.push_back(std::async(std::launch::async, [&input]() {
            return CalculateInterfaceProbeV1(input).event_mask;
        }));
    }
    for (auto& task : tasks) {
        assert(task.get() == result.event_mask);
    }

    std::vector<std::future<std::vector<float>>> formal_tasks;
    for (int index = 0; index < 16; ++index) {
        formal_tasks.push_back(std::async(
            std::launch::async,
            [&formal_input, &mirrored_formal_input, index]() {
                const TdxSeriesView& selected = index % 2 == 0
                    ? formal_input
                    : mirrored_formal_input;
                return CalculateSegmentEventsV1(selected).event_mask;
            }
        ));
    }
    for (std::size_t index = 0; index < formal_tasks.size(); ++index) {
        const auto& expected = index % 2U == 0U
            ? formal_result.event_mask
            : mirrored_formal_result.event_mask;
        assert(formal_tasks[index].get() == expected);
    }

    return 0;
}
