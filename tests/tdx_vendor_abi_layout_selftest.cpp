#include "PluginTCalcFunc.h"

#include <cstddef>
#include <type_traits>

static_assert(
    sizeof(PluginTCalcFuncInfo::nFuncMark) == sizeof(unsigned short),
    "TongdaXin ABI requires a 16-bit function mark."
);
static_assert(
    std::is_same_v<
        decltype(PluginTCalcFuncInfo::nFuncMark),
        unsigned short
    >,
    "TongdaXin ABI requires nFuncMark to be unsigned short."
);
static_assert(
    offsetof(PluginTCalcFuncInfo, nFuncMark) == 0U,
    "TongdaXin ABI requires nFuncMark at byte offset 0."
);
static_assert(
    offsetof(PluginTCalcFuncInfo, pCallFunc) == sizeof(unsigned short),
    "TongdaXin ABI requires pCallFunc at byte offset 2."
);
static_assert(
    sizeof(PluginTCalcFuncInfo)
        == sizeof(unsigned short) + sizeof(pPluginFUNC),
    "TongdaXin ABI requires a packed PluginTCalcFuncInfo."
);

int main() {
    return 0;
}
