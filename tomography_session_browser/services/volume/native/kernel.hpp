#pragma once
#include <cstdint>

namespace tomography {
struct Grid {
    const unsigned char *data;
    int64_t nx, ny, nz, sx, sy, sz;
    int mode;
    bool swap;
};

// Buffers are validated and held by the Python binding for the entire call.
int reslice(const Grid &grid, const double *matrix, const double *offset,
            int h, int v, int n, int first, int last, int top, int end,
            int threads, float *output, double **planes, const int *fresh) noexcept;
}
