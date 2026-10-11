#pragma once

// Project-authored minimal TongdaXin ABI compatibility declarations.
// Not an official vendor SDK. See docs/provenance.md.
// One-byte packing and a 16-bit function mark are required by the host ABI.

#if !defined(_WIN32)
#error "TongdaXin formula DLLs are supported only on Windows."
#endif

#include <Windows.h>

#pragma pack(push, 1)

typedef void (*pPluginFUNC)(
    int DataLen,
    float* pfOUT,
    float* pfINa,
    float* pfINb,
    float* pfINc
);

typedef struct tagPluginTCalcFuncInfo {
    unsigned short nFuncMark;
    pPluginFUNC pCallFunc;
} PluginTCalcFuncInfo;

typedef BOOL (*pRegisterPluginFUNC)(PluginTCalcFuncInfo**);

#pragma pack(pop)
