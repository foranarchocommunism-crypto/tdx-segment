#include "tdx_segment/tdx_segment.hpp"
#include "PluginTCalcFunc.h"

#include <Windows.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>

namespace {

bool HasBit(float encoded, std::uint16_t bit) {
    const auto value = static_cast<std::uint16_t>(encoded);
    return (value & bit) != 0U;
}

int Fail(const char* message, HMODULE module = nullptr) {
    std::cerr << message << '\n';
    if (module != nullptr) {
        FreeLibrary(module);
    }
    return 1;
}

}  // namespace

int wmain(int argc, wchar_t* argv[]) {
    if (argc != 2) {
        return Fail("Expected the DLL path as the only argument.");
    }

    const HMODULE module = LoadLibraryW(argv[1]);
    if (module == nullptr) {
        return Fail("LoadLibraryW failed.");
    }

    const FARPROC raw_register = GetProcAddress(module, "RegisterTdxFunc");
    if (raw_register == nullptr) {
        return Fail("RegisterTdxFunc is not exported by exact name.", module);
    }
    const auto register_function = reinterpret_cast<pRegisterPluginFUNC>(
        raw_register
    );

    PluginTCalcFuncInfo* functions = nullptr;
    if (register_function(&functions) == FALSE || functions == nullptr) {
        return Fail("RegisterTdxFunc did not return a function table.", module);
    }
    PluginTCalcFuncInfo* const registered_functions = functions;
    if (
        register_function(&functions) != FALSE
        || functions != registered_functions
    ) {
        return Fail(
            "RegisterTdxFunc must reject an already initialized table.",
            module
        );
    }
    if (register_function(nullptr) != FALSE) {
        return Fail("RegisterTdxFunc must reject a null output pointer.", module);
    }

    pPluginFUNC probe = nullptr;
    pPluginFUNC formal = nullptr;
    pPluginFUNC recovery = nullptr;
    std::size_t registered_count = 0U;
    for (std::size_t index = 0; functions[index].nFuncMark != 0U; ++index) {
        ++registered_count;
        if (functions[index].nFuncMark == tdx_segment::kRecoveryExperimentFunctionId) {
            if (recovery != nullptr) {return Fail("Function 2 registered twice.", module);}
            recovery = functions[index].pCallFunc;
        }
        if (
            functions[index].nFuncMark
            == tdx_segment::kInterfaceProbeFunctionId
        ) {
            if (probe != nullptr) {
                return Fail("Function 99 was registered more than once.", module);
            }
            probe = functions[index].pCallFunc;
        }
        if (
            functions[index].nFuncMark
            == tdx_segment::kFormalSegmentFunctionId
        ) {
            if (formal != nullptr) {
                return Fail("Function 1 was registered more than once.", module);
            }
            formal = functions[index].pCallFunc;
        }
    }
    if (registered_count != 5U) {
        return Fail("The function table must contain functions 1, 2, 10, 11 and 99.", module);
    }
    if (functions[registered_count].pCallFunc != nullptr) {
        return Fail("The function table terminator must have a null callback.", module);
    }
    if (probe == nullptr) {
        return Fail("Function 99 was not registered.", module);
    }
    if (recovery == nullptr) {return Fail("Function 2 was not registered.", module);}
    if (formal == nullptr) {
        return Fail("Function 1 was not registered.", module);
    }

    float markers[] = {-1.0F, 0.0F, 1.0F, -1.0F, 1.0F};
    float highs[] = {1.0F, 2.0F, 5.0F, 3.0F, 8.0F};
    float lows[] = {0.0F, 1.0F, 4.0F, 1.0F, 7.0F};
    float output[] = {-9.0F, -9.0F, -9.0F, -9.0F, -9.0F};
    probe(5, output, markers, highs, lows);
    if (!HasBit(output[0], tdx_segment::ProbeBottomEndpoint)) {
        return Fail("Probe did not echo the first bottom endpoint.", module);
    }
    if (!HasBit(output[4], tdx_segment::ProbeCallSucceeded)) {
        return Fail("Probe did not emit the successful-call bit.", module);
    }
    if (HasBit(output[4], tdx_segment::ProbeInputError)) {
        return Fail("Probe unexpectedly reported an input error.", module);
    }

    float bad_markers[] = {-1.0F, 0.25F, 1.0F};
    float bad_highs[] = {1.0F, 2.0F, 5.0F};
    float bad_lows[] = {0.0F, 1.0F, 4.0F};
    float bad_output[] = {-9.0F, -9.0F, -9.0F};
    probe(3, bad_output, bad_markers, bad_highs, bad_lows);
    if (
        bad_output[0] != 0.0F
        || bad_output[1]
            != static_cast<float>(tdx_segment::ProbeErrorAtBar)
        || bad_output[2]
            != static_cast<float>(
                tdx_segment::ProbeInputError
                | (
                    static_cast<std::uint16_t>(
                        tdx_segment::InputErrorCode::InvalidEndpointMarker
                    )
                    << tdx_segment::kProbeErrorCodeShift
                )
            )
    ) {
        return Fail("Probe did not encode invalid input atomically.", module);
    }

    // [0,5,2,8,4,6,1] 的最早三笔建立向上线段；顶 8 在第 4 根
    // K 线，最后一根向下笔跌破标准 B.low 后于第 7 根 K 线确认。
    std::array<float, 7U> formal_markers{
        -1.0F, 1.0F, -1.0F, 1.0F, -1.0F, 1.0F, -1.0F,
    };
    std::array<float, 7U> formal_highs{
        0.0F, 5.0F, 2.0F, 8.0F, 4.0F, 6.0F, 1.0F,
    };
    std::array<float, 7U> formal_lows = formal_highs;
    std::array<float, 7U> formal_output{
        -9.0F, -9.0F, -9.0F, -9.0F, -9.0F, -9.0F, -9.0F,
    };
    formal(
        static_cast<int>(formal_output.size()),
        formal_output.data(),
        formal_markers.data(),
        formal_highs.data(),
        formal_lows.data()
    );
    constexpr std::array<float, 7U> expected_formal{
        static_cast<float>(tdx_segment::UpSegmentChainStart),
        0.0F,
        0.0F,
        static_cast<float>(tdx_segment::UpSegmentEndpoint),
        0.0F,
        0.0F,
        static_cast<float>(tdx_segment::UpSegmentConfirmed),
    };
    if (formal_output != expected_formal) {
        return Fail("Function 1 returned an unexpected event certificate.", module);
    }

    std::array<float, 3U> formal_bad_output{-9.0F, -9.0F, -9.0F};
    formal(
        static_cast<int>(formal_bad_output.size()),
        formal_bad_output.data(),
        bad_markers,
        bad_highs,
        bad_lows
    );
    constexpr std::array<float, 3U> expected_bad_formal{
        0.0F,
        0.0F,
        static_cast<float>(tdx_segment::SegmentInputError),
    };
    if (formal_bad_output != expected_bad_formal) {
        return Fail("Function 1 did not encode invalid input atomically.", module);
    }

    recovery(static_cast<int>(formal_output.size()), formal_output.data(), formal_markers.data(), formal_highs.data(), formal_lows.data());
    if (formal_output != expected_formal) {return Fail("Function 2 changed an ordinary short certificate.", module);}
    recovery(static_cast<int>(formal_bad_output.size()), formal_bad_output.data(), bad_markers, bad_highs, bad_lows);
    if (formal_bad_output != expected_bad_formal) {return Fail("Function 2 input errors are not atomic.", module);}

    // Exercise early return and decoder errors through the real registered ABI.
    for (const auto callback : {formal, recovery, probe}) {
        std::array<float, 3U> early_output{-3.0F, -5.0F, -7.0F};
        callback(0, early_output.data(), nullptr, nullptr, nullptr);
        callback(-1, early_output.data(), nullptr, nullptr, nullptr);
        callback(3, nullptr, nullptr, nullptr, nullptr);
        if (early_output != std::array<float, 3U>{-3.0F, -5.0F, -7.0F}) {
            return Fail("Invalid length or null output modified the caller buffer.", module);
        }
        callback(3, early_output.data(), nullptr, nullptr, nullptr);
        const auto expected_error = callback == probe
            ? static_cast<std::uint16_t>(tdx_segment::ProbeInputError
                | (static_cast<std::uint16_t>(tdx_segment::InputErrorCode::NullInputArray)
                   << tdx_segment::kProbeErrorCodeShift))
            : static_cast<std::uint16_t>(tdx_segment::SegmentInputError);
        if (early_output != std::array<float, 3U>{0.0F, 0.0F, static_cast<float>(expected_error)}) {
            return Fail("Null arrays did not produce the exact decoder error through the ABI.", module);
        }
    }
    FreeLibrary(module);
    return 0;
}
