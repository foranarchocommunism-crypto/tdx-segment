// Real registered DLL callback runner; never links the segment core.
#include "PluginTCalcFunc.h"
#include <array>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

int wmain(int argc, wchar_t** argv) {
    if (argc != 2) {
        std::cerr << "Usage: tdx_dll_runner.exe absolute-dll-path\n"
                     "stdin: call-count; for each call: selector length, then three float arrays\n";
        return 2;
    }
    HMODULE module = LoadLibraryW(argv[1]);
    if (module == nullptr) {
        std::cerr << "LoadLibrary failed: " << GetLastError() << '\n';
        return 3;
    }
    try {
        const auto register_function = reinterpret_cast<pRegisterPluginFUNC>(
            GetProcAddress(module, "RegisterTdxFunc"));
        PluginTCalcFuncInfo* table = nullptr;
        if (register_function == nullptr || !register_function(&table) || table == nullptr) {
            throw std::runtime_error("Invalid RegisterTdxFunc result");
        }
        std::map<unsigned short, pPluginFUNC> functions;
        bool terminated = false;
        for (std::size_t index = 0; index < 256; ++index) {
            if (table[index].nFuncMark == 0) { terminated = true; break; }
            if (table[index].pCallFunc == nullptr ||
                !functions.emplace(table[index].nFuncMark, table[index].pCallFunc).second) {
                throw std::runtime_error("Invalid or duplicate callback");
            }
        }
        if (!terminated) throw std::runtime_error("Unterminated callback table");
        int calls = 0;
        if (!(std::cin >> calls) || calls < 0) throw std::runtime_error("Invalid call count");
        std::cout << std::setprecision(std::numeric_limits<float>::max_digits10);
        for (int call = 0; call < calls; ++call) {
            unsigned int selector = 0;
            int length = 0;
            if (!(std::cin >> selector >> length) || selector > 65535 || length < 0 || length > 1048576) {
                throw std::runtime_error("Invalid selector or length");
            }
            const auto callback = functions.find(static_cast<unsigned short>(selector));
            if (callback == functions.end()) throw std::runtime_error("Unregistered selector");
            std::array<std::vector<float>, 3> inputs;
            for (auto& input : inputs) {
                input.resize(static_cast<std::size_t>(length));
                for (float& value : input) {
                    if (!(std::cin >> value)) throw std::runtime_error("Incomplete input array");
                }
            }
            // NaN sentinel exposes unwritten callback output instead of masking it with zeros.
            std::vector<float> output(static_cast<std::size_t>(length), std::numeric_limits<float>::quiet_NaN());
            callback->second(length, output.data(), inputs[0].data(), inputs[1].data(), inputs[2].data());
            std::cout << length;
            for (float value : output) std::cout << ' ' << value;
            std::cout << '\n';
        }
        FreeLibrary(module);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        FreeLibrary(module);
        return 4;
    }
}
