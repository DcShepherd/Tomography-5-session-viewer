// Full-quality trilinear sampling; no filesystem, cache or Qt ownership.
#include "kernel.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <thread>
#include <vector>

namespace tomography {
// Explicit IEEE binary16 decoding works with MSVC, GCC and Clang, without
// CPU-specific instructions or a full-volume float32 conversion.
struct Half {
    uint16_t bits;
    explicit operator double() const {
        const uint32_t sign = uint32_t(bits & 0x8000) << 16;
        const uint32_t exponent = (bits >> 10) & 31, fraction = bits & 1023;
        if (!exponent) return (sign ? -1. : 1.) * std::ldexp(double(fraction), -24);
        const uint32_t word = sign | ((exponent == 31 ? 255 : exponent + 112) << 23) | (fraction << 13);
        float value;
        std::memcpy(&value, &word, sizeof(value));
        return value;
    }
};

template<class T> static T read(const unsigned char *p, bool swap) {
    unsigned char bytes[sizeof(T)];
    for (unsigned i=0; i<sizeof(T); ++i)
        bytes[i] = p[swap ? sizeof(T)-1-i : i];
    T value;
    std::memcpy(&value, bytes, sizeof(T));
    return value;
}

template<class T> static double sample(const Grid &g, const double *m,
                                       const double *offset, const double *q) {
    const int64_t dimensions[] = {g.nx,g.ny,g.nz};
    int64_t lo[3], hi[3];
    double fraction[3];
    for (int a=0; a<3; ++a) {
        double p = ((m[3*a]*q[0] + m[3*a+1]*q[1]) + m[3*a+2]*q[2]) + offset[a];
        if (!std::isfinite(p) || p < -1e-8 || p > dimensions[a]-1+1e-8)
            return std::numeric_limits<double>::quiet_NaN();
        p = std::clamp(p, 0., double(dimensions[a]-1));
        lo[a] = int64_t(std::floor(p));
        hi[a] = std::min(lo[a]+1, dimensions[a]-1);
        fraction[a] = p-lo[a];
    }
    double value = 0.;
    for (int x=0; x<2; ++x) for (int y=0; y<2; ++y) for (int z=0; z<2; ++z) {
        const double weight = ((x ? fraction[0] : 1-fraction[0]) *
                               (y ? fraction[1] : 1-fraction[1])) *
                               (z ? fraction[2] : 1-fraction[2]);
        if (weight <= 1e-15) continue;
        const int64_t index = (x ? hi[0] : lo[0])*g.sx +
                              (y ? hi[1] : lo[1])*g.sy +
                              (z ? hi[2] : lo[2])*g.sz;
        const double v = double(read<T>(g.data+index, g.swap));
        if (!std::isfinite(v)) return std::numeric_limits<double>::quiet_NaN();
        value += v*weight;
    }
    return value;
}

template<class F> static void rows(int top, int end, int threads, F work) {
    threads = std::clamp(threads, 1, std::max(1,end-top));
    std::vector<std::thread> workers;
    try {
        for (int t=1; t<threads; ++t)
            workers.emplace_back(work, top+(end-top)*t/threads, top+(end-top)*(t+1)/threads);
    } catch (...) {
        for (auto &worker: workers) worker.join();
        throw;
    }
    work(top,top+(end-top)/threads);
    for (auto &worker: workers) worker.join();
}

template<class T> static void run(const Grid &g, const double *m, const double *offset,
        int h, int v, int n, int first, int last, int top, int end, int threads,
        float *output, double **planes, const int *fresh) {
    const int64_t dimensions[] = {g.nx,g.ny,g.nz};
    const int width = int(dimensions[h]);
    rows(top,end,threads,[&](int begin,int stop) {
        for (int row=begin; row<stop; ++row) for (int col=0; col<width; ++col) {
            double q[3] = {0.,0.,0.};
            q[h] = col; q[v] = row;
            double total=0.; int hits=0;
            for (int plane=first; plane<last; ++plane) {
                q[n] = plane;
                const int64_t index=int64_t(row)*width+col;
                const int slot=plane-first;
                const double value=(planes && !fresh[slot]) ? planes[slot][index] : sample<T>(g,m,offset,q);
                if (planes && fresh[slot]) planes[slot][index] = value;
                if (std::isfinite(value)) { total += value; ++hits; }
            }
            if (output) output[int64_t(row)*width+col] = hits ? float(total/hits) :
                std::numeric_limits<float>::quiet_NaN();
        }
    });
}

// A bounded row block is one foreign call; Python checks cancellation between blocks.
int reslice(const Grid &grid, const double *m, const double *offset,
        int h, int v, int n, int first, int last, int top, int end, int threads,
        float *output, double **planes, const int *fresh) noexcept {
    const Grid *g = &grid;
    try {
        switch(g->mode) {
        case 0: run<int8_t>(*g,m,offset,h,v,n,first,last,top,end,threads,output,planes,fresh); break;
        case 1: run<int16_t>(*g,m,offset,h,v,n,first,last,top,end,threads,output,planes,fresh); break;
        case 2: run<float>(*g,m,offset,h,v,n,first,last,top,end,threads,output,planes,fresh); break;
        case 6: run<uint16_t>(*g,m,offset,h,v,n,first,last,top,end,threads,output,planes,fresh); break;
        case 12: run<Half>(*g,m,offset,h,v,n,first,last,top,end,threads,output,planes,fresh); break;
        default: return 1;
        }
        return 0;
    } catch (...) { return 2; }
}


}
