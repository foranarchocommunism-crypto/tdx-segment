#pragma once

// Private vector primitives for the frozen pen formula, not a general TDX SDK.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <vector>

namespace tdx_segment::pen_formula {

inline constexpr float invalid = std::numeric_limits<float>::quiet_NaN();

struct Series {
    std::vector<float> values;
    explicit Series(std::size_t size, float fill = 0.0F) : values(size, fill) {}
    Series(const float* data, std::size_t size) : values(data, data + size) {}
    float operator[](std::size_t i) const { return values[i]; }
    std::size_t size() const { return values.size(); }
};
inline bool truth(float value) { return std::isfinite(value) && value != 0.0F; }
inline float at(const Series& value, std::size_t i) { return value[i]; }
inline float at(float value, std::size_t) { return value; }
inline float at(int value, std::size_t) { return static_cast<float>(value); }

template<class Left, class Right, class Operation>
Series binary(const Left& left, const Right& right, std::size_t size, Operation op) {
    Series result(size);
    for (std::size_t i = 0; i < size; ++i) {
        const float a = at(left, i), b = at(right, i);
        result.values[i] = std::isfinite(a) && std::isfinite(b) ? op(a, b) : invalid;
    }
    return result;
}
#define TDX_SERIES_OPERATOR(symbol, expression) \
inline Series operator symbol(const Series& a, const Series& b) { \
    return binary(a, b, a.size(), [](float x, float y) { return static_cast<float>(expression); }); } \
inline Series operator symbol(const Series& a, float b) { \
    return binary(a, b, a.size(), [](float x, float y) { return static_cast<float>(expression); }); } \
inline Series operator symbol(float a, const Series& b) { \
    return binary(a, b, b.size(), [](float x, float y) { return static_cast<float>(expression); }); }
TDX_SERIES_OPERATOR(+, x + y)
TDX_SERIES_OPERATOR(-, x - y)
TDX_SERIES_OPERATOR(>, x > y)
TDX_SERIES_OPERATOR(<, x < y)
TDX_SERIES_OPERATOR(>=, x >= y)
TDX_SERIES_OPERATOR(<=, x <= y)
TDX_SERIES_OPERATOR(==, x == y)
TDX_SERIES_OPERATOR(!=, x != y)
TDX_SERIES_OPERATOR(&&, truth(x) && truth(y))
TDX_SERIES_OPERATOR(||, truth(x) || truth(y))
#undef TDX_SERIES_OPERATOR

template<class Yes, class No>
Series IF(const Series& condition, const Yes& yes, const No& no) {
    Series result(condition.size());
    for (std::size_t i = 0; i < result.size(); ++i) {
        result.values[i] = std::isfinite(condition[i])
            ? (truth(condition[i]) ? at(yes, i) : at(no, i)) : invalid;
    }
    return result;
}
inline Series ISVALID(const Series& input) {
    Series result(input.size());
    for (std::size_t i = 0; i < result.size(); ++i) {
        result.values[i] = std::isfinite(input[i]) ? 1.0F : 0.0F;
    }
    return result;
}
inline Series MA(const Series& input, int period) {
    Series result(input.size(), invalid);
    for (std::size_t i = static_cast<std::size_t>(period - 1); i < input.size(); ++i) {
        double total = 0.0;
        for (int j = 0; j < period; ++j) { total += input[i - static_cast<std::size_t>(j)]; }
        result.values[i] = static_cast<float>(total / period);
    }
    return result;
}
inline Series BARSLAST(const Series& input) {
    Series result(input.size());
    std::size_t elapsed = 0;
    for (std::size_t i = 0; i < input.size(); ++i) {
        elapsed = truth(input[i]) ? 0U : elapsed + 1U;
        result.values[i] = static_cast<float>(elapsed);
    }
    return result;
}
template<class Period>
Series REF(const Series& input, const Period& period) {
    Series result(input.size(), invalid);
    for (std::size_t i = 0; i < input.size(); ++i) {
        const float n = at(period, i);
        if (std::isfinite(n) && n >= 0.0F && static_cast<double>(n) <= static_cast<double>(i)) {
            result.values[i] = input[i - static_cast<std::size_t>(n)];
        }
    }
    return result;
}
inline Series BACKSET(const Series& input, int count) {
    std::vector<int> delta(input.size() + 1U);
    for (std::size_t i = 0; i < input.size(); ++i) {
        if (truth(input[i])) {
            const auto width = static_cast<std::size_t>(count);
            ++delta[i + 1U > width ? i + 1U - width : 0U];
            --delta[i + 1U];
        }
    }
    Series result(input.size());
    int active = 0;
    for (std::size_t i = 0; i < input.size(); ++i) {
        active += delta[i]; result.values[i] = active > 0 ? 1.0F : 0.0F;
    }
    return result;
}
template<class Period>
Series COUNT(const Series& input, const Period& period) {
    std::vector<std::size_t> prefix(input.size() + 1U);
    for (std::size_t i = 0; i < input.size(); ++i) { prefix[i + 1U] = prefix[i] + (truth(input[i]) ? 1U : 0U); }
    Series result(input.size(), invalid);
    for (std::size_t i = 0; i < input.size(); ++i) {
        const float n = at(period, i);
        if (!std::isfinite(n)) { continue; }
        const auto start = n <= 0.0F || static_cast<double>(n) >= static_cast<double>(i + 1U)
            ? 0U : i + 1U - static_cast<std::size_t>(n);
        result.values[i] = static_cast<float>(prefix[i + 1U] - prefix[start]);
    }
    return result;
}
template<bool Maximum, class Period>
Series extreme(const Series& input, const Period& period) {
    // Windows can move backwards when N=0: a range tree also handles that case.
    const auto combine = [](float a, float b) { return Maximum ? std::max(a, b) : std::min(a, b); };
    const float empty = Maximum ? -std::numeric_limits<float>::infinity() : std::numeric_limits<float>::infinity();
    std::size_t width = 1U;
    while (width < input.size()) { width *= 2U; }
    std::vector<float> tree(width * 2U, empty);
    for (std::size_t i = 0; i < input.size(); ++i) { tree[width + i] = std::isfinite(input[i]) ? input[i] : empty; }
    for (std::size_t i = width - 1U; i > 0U; --i) { tree[i] = combine(tree[i * 2U], tree[i * 2U + 1U]); }
    Series result(input.size(), invalid);
    for (std::size_t i = 0; i < input.size(); ++i) {
        const float n = at(period, i);
        if (!std::isfinite(n) || n < 0.0F) { continue; }
        const auto start = n == 0.0F || static_cast<double>(n) >= static_cast<double>(i + 1U)
            ? 0U : i + 1U - static_cast<std::size_t>(n);
        std::size_t left = width + start, right = width + i + 1U;
        float value = empty;
        while (left < right) {
            if (left % 2U != 0U) { value = combine(value, tree[left++]); }
            if (right % 2U != 0U) { value = combine(value, tree[--right]); }
            left /= 2U; right /= 2U;
        }
        result.values[i] = std::isfinite(value) ? value : invalid;
    }
    return result;
}
template<class Period> Series HHV(const Series& x, const Period& n) { return extreme<true>(x, n); }
template<class Period> Series LLV(const Series& x, const Period& n) { return extreme<false>(x, n); }

}  // namespace tdx_segment::pen_formula
