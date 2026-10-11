#include "tdx_segment/tdx_segment.hpp"

#include <cstdint>
#include <string>
#include <exception>
#include <iostream>
#include <vector>

namespace {

bool ReadSeries(std::vector<float>& values, std::int32_t length) {
    values.resize(static_cast<std::size_t>(length));
    for (float& value : values) {
        if (!(std::cin >> value)) {
            return false;
        }
    }
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    const bool recovery = argc > 1 && std::string(argv[1]) == "--recovery";
    const bool baseline = argc > 1 && std::string(argv[1]) == "--baseline";
    std::int32_t case_count = 0;
    if (!(std::cin >> case_count) || case_count < 0) {
        std::cerr << "Invalid case count.\n";
        return 1;
    }

    try {
        for (std::int32_t case_index = 0; case_index < case_count;
             ++case_index) {
            std::int32_t data_len = 0;
            if (!(std::cin >> data_len) || data_len < 0) {
                std::cerr << "Invalid data length at case " << case_index << ".\n";
                return 2;
            }

            std::vector<float> markers;
            std::vector<float> highs;
            std::vector<float> lows;
            if (
                !ReadSeries(markers, data_len)
                || !ReadSeries(highs, data_len)
                || !ReadSeries(lows, data_len)
            ) {
                std::cerr << "Incomplete series at case " << case_index << ".\n";
                return 3;
            }

            const tdx_segment::CalculationResult result =
                (baseline ? tdx_segment::CalculateSegmentEventsWithoutRecovery :
                 recovery ? tdx_segment::CalculateSegmentEventsV2 : tdx_segment::CalculateSegmentEventsV1)(
                    tdx_segment::TdxSeriesView{
                        data_len,
                        markers.data(),
                        highs.data(),
                        lows.data(),
                    }
                );
            std::cout
                << static_cast<unsigned int>(result.status) << ' '
                << static_cast<unsigned int>(result.error) << ' '
                << result.error_bar << ' '
                << result.event_mask.size();
            for (float value : result.event_mask) {
                std::cout << ' ' << static_cast<unsigned int>(value);
            }
            std::cout << '\n';
        }
    } catch (const std::exception& error) {
        std::cerr << "Engine exception: " << error.what() << '\n';
        return 4;
    } catch (...) {
        std::cerr << "Unknown engine exception.\n";
        return 5;
    }
    return 0;
}
