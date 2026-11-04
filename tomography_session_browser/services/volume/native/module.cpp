#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "kernel.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <vector>
#if defined(_M_X64) || defined(__x86_64__)
#if defined(_MSC_VER)
#include <intrin.h>
#else
#include <cpuid.h>
#endif
#endif

// One source builds the portable module and, on x86-64, an x86-64-v3 variant.
#ifdef TOMOAPP_RESLICE_V3
#define RESLICE_MODULE "_reslice_v3"
#define RESLICE_INIT PyInit__reslice_v3
#else
#define RESLICE_MODULE "_reslice"
#define RESLICE_INIT PyInit__reslice
#endif

namespace {
// One call samples at most this many rows; Python checks cancellation between calls.
constexpr int MAX_TILE_ROWS = 128;
constexpr int MAX_WORKERS = 8;
struct Buffer {
    Py_buffer view{};
    Buffer() = default;
    Buffer(const Buffer &) = delete;
    Buffer &operator=(const Buffer &) = delete;
    ~Buffer() { if (view.obj) PyBuffer_Release(&view); }
    bool get(PyObject *object, bool writable = false) {
        return PyObject_GetBuffer(object, &view, PyBUF_FORMAT | PyBUF_STRIDES |
                                  (writable ? PyBUF_WRITABLE : 0)) == 0;
    }
};

bool fail(const char *message) { PyErr_SetString(PyExc_ValueError, message); return false; }

char format(const Py_buffer &b, bool &swap) {
    if (!b.format) return 0;
    const char *f = b.format;
    const uint16_t one = 1;
    const bool little = *reinterpret_cast<const unsigned char *>(&one) == 1;
    swap = (*f == '>' || *f == '!') ? little : (*f == '<' && !little);
    if (*f == '<' || *f == '>' || *f == '!' || *f == '=' || *f == '@') ++f;
    return f[0] && !f[1] ? f[0] : 0;
}

bool dense(const Py_buffer &b) {
    if (!b.shape || !b.strides || b.suboffsets || b.ndim < 1 || b.ndim > 3)
        return fail("Expected a direct dense array with one to three axes");
    std::array<std::pair<Py_ssize_t, Py_ssize_t>, 3> axes{};
    for (int i=0; i<b.ndim; ++i) {
        if (b.shape[i] < 1 || b.strides[i] < 1) return fail("Invalid array shape or stride");
        axes[i] = {b.strides[i], b.shape[i]};
    }
    std::sort(axes.begin(), axes.begin()+b.ndim);
    Py_ssize_t size = b.itemsize;
    for (int i=0; i<b.ndim; ++i) {
        if (axes[i].second == 1) continue;
        if (axes[i].first != size || size > PY_SSIZE_T_MAX/axes[i].second)
            return fail("The source must be a dense axis permutation");
        size *= axes[i].second;
    }
    return size == b.len || fail("Array size does not match its shape");
}

bool plane(const Py_buffer &b, int height, int width, char type, int itemsize) {
    bool swap = false;
    return (b.ndim == 2 && b.shape && b.shape[0] == height && b.shape[1] == width &&
            b.itemsize == itemsize && format(b, swap) == type && !swap &&
            reinterpret_cast<uintptr_t>(b.buf) % itemsize == 0 &&
            PyBuffer_IsContiguous(&b, 'C')) || fail("Invalid native output or slab plane");
}

bool overlaps(const Py_buffer &a, const Py_buffer &b) {
    const auto x = reinterpret_cast<uintptr_t>(a.buf), y = reinterpret_cast<uintptr_t>(b.buf);
    return x <= y ? y-x < uintptr_t(a.len) : x-y < uintptr_t(b.len);
}

// The x86-64-v3 level (the v2 set plus AVX, AVX2, BMI1/2, F16C, FMA, LZCNT and
// MOVBE), with AVX state saved by the operating system. The portable module
// reports it, so Python never loads the v3 build where it would fault.
bool x86_64_v3() {
#if defined(_M_X64) || defined(__x86_64__)
    auto cpuid = [](unsigned leaf, unsigned sub, unsigned r[4]) {
#if defined(_MSC_VER)
        int values[4];
        __cpuidex(values, int(leaf), int(sub));
        for (int i=0; i<4; ++i) r[i] = unsigned(values[i]);
#else
        __cpuid_count(leaf, sub, r[0], r[1], r[2], r[3]);
#endif
    };
    unsigned r[4];
    cpuid(0, 0, r);
    if (r[0] < 7) return false;
    cpuid(0x80000000u, 0, r);
    if (r[0] < 0x80000001u) return false;
    cpuid(0x80000001u, 0, r);
    if (!(r[2] & 1u) || !(r[2] & (1u<<5))) return false;  // LAHF/SAHF, LZCNT
    cpuid(1, 0, r);
    // SSE3, SSSE3, FMA, CX16, SSE4.1, SSE4.2, MOVBE, POPCNT, OSXSAVE, AVX, F16C
    constexpr unsigned leaf1 = (1u<<0)|(1u<<9)|(1u<<12)|(1u<<13)|(1u<<19)|(1u<<20)|
                               (1u<<22)|(1u<<23)|(1u<<27)|(1u<<28)|(1u<<29);
    if ((r[2] & leaf1) != leaf1) return false;
    cpuid(7, 0, r);
    constexpr unsigned leaf7 = (1u<<3)|(1u<<5)|(1u<<8);  // BMI1, AVX2, BMI2
    if ((r[1] & leaf7) != leaf7) return false;
#if defined(_MSC_VER)
    const unsigned long long xcr0 = _xgetbv(0);
#else
    unsigned low, high;
    __asm__ volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));
    const unsigned long long xcr0 = (static_cast<unsigned long long>(high) << 32) | low;
#endif
    return (xcr0 & 6) == 6;  // XMM and YMM registers
#else
    return false;
#endif
}

PyObject *sample(PyObject *, PyObject *args) {
    PyObject *source, *matrix, *offset, *output, *planes, *fresh;
    int h,v,n,first,last,top,end,threads;
    if (!PyArg_ParseTuple(args, "OOOiiiiiiiiOOO", &source,&matrix,&offset,
                          &h,&v,&n,&first,&last,&top,&end,&threads,&output,&planes,&fresh)) return nullptr;
    Buffer input, transform, translation, target;
    if (!input.get(source) || !transform.get(matrix) || !translation.get(offset) || !target.get(output,true)) return nullptr;
    auto &b = input.view;
    if (b.ndim != 3 || !dense(b)) { if (!PyErr_Occurred()) fail("Expected a 3D source"); return nullptr; }
    bool swap = false;
    const char type = format(b,swap);
    const int mode = type=='b' && b.itemsize==1 ? 0 : type=='h' && b.itemsize==2 ? 1 :
                     type=='f' && b.itemsize==4 ? 2 : type=='H' && b.itemsize==2 ? 6 :
                     type=='e' && b.itemsize==2 ? 12 : -1;
    if (mode < 0) { fail("Unsupported source dtype"); return nullptr; }
    const Py_ssize_t dims[] = {b.shape[2],b.shape[1],b.shape[0]};
    if (h<0 || h>2 || v<0 || v>2 || n<0 || n>2 || h==v || h==n || v==n ||
        dims[0]>1000000 || dims[1]>1000000 || dims[2]>1000000 ||
        first<0 || last<=first || last>dims[n] || last-first>101 ||
        top<0 || end<=top || end>dims[v] || end-top>MAX_TILE_ROWS || threads<1 || threads>MAX_WORKERS) {
        fail("Invalid sampling axes, slab, tile or worker count"); return nullptr;
    }
    if (!plane(target.view,int(dims[v]),int(dims[h]),'f',4) || overlaps(b,target.view)) {
        if (!PyErr_Occurred()) fail("Output must not alias the source"); return nullptr;
    }
    bool swapped = false;
    if (transform.view.ndim!=2 || !transform.view.shape || transform.view.shape[0]!=3 || transform.view.shape[1]!=3 ||
        transform.view.itemsize!=8 || format(transform.view,swapped)!='d' || swapped || !PyBuffer_IsContiguous(&transform.view,'C') ||
        translation.view.ndim!=1 || !translation.view.shape || translation.view.shape[0]!=3 ||
        translation.view.len!=24 || translation.view.itemsize!=8 || format(translation.view,swapped)!='d' || swapped ||
        !PyBuffer_IsContiguous(&translation.view,'C') || reinterpret_cast<uintptr_t>(transform.view.buf)%8 ||
        reinterpret_cast<uintptr_t>(translation.view.buf)%8 || overlaps(target.view,transform.view) ||
        overlaps(target.view,translation.view)) { fail("Expected separate aligned native float64 affine arrays"); return nullptr; }
    const auto *m = static_cast<const double *>(transform.view.buf), *o = static_cast<const double *>(translation.view.buf);
    for (int i=0; i<9; ++i) if (!std::isfinite(m[i])) { fail("Non-finite transform"); return nullptr; }
    for (int i=0; i<3; ++i) if (!std::isfinite(o[i])) { fail("Non-finite offset"); return nullptr; }
    const int count=last-first;
    // Fixed bounds avoid moving Py_buffer owners or allocating while the GIL is released.
    Buffer cached[101]; double *pointers[101]; int fill[101];
    if (planes != Py_None) {
        if (!PyTuple_Check(planes) || !PyTuple_Check(fresh) || PyTuple_Size(planes)!=count || PyTuple_Size(fresh)!=count) {
            fail("Slab buffers and flags must match the requested planes"); return nullptr;
        }
        for (int i=0; i<count; ++i) {
            PyObject *flag=PyTuple_GetItem(fresh,i);
            if (!PyBool_Check(flag)) { fail("Slab fill flags must be booleans"); return nullptr; }
            fill[i]=flag==Py_True;
            if (!cached[i].get(PyTuple_GetItem(planes,i),fill[i]) ||
                !plane(cached[i].view,int(dims[v]),int(dims[h]),'d',8)) return nullptr;
            if (overlaps(cached[i].view,b) || overlaps(cached[i].view,target.view) ||
                overlaps(cached[i].view,transform.view) || overlaps(cached[i].view,translation.view)) {
                fail("Slab planes must not alias source or output"); return nullptr;
            }
            for (int j=0; j<i; ++j) if (overlaps(cached[i].view,cached[j].view)) {
                fail("Slab planes must not alias each other"); return nullptr;
            }
            pointers[i]=static_cast<double *>(cached[i].view.buf);
        }
    } else if (fresh != Py_None) { fail("Unexpected slab fill flags"); return nullptr; }
    tomography::Grid grid{static_cast<const unsigned char *>(b.buf),dims[0],dims[1],dims[2],
                          b.strides[2],b.strides[1],b.strides[0],mode,swap};
    int status;
    Py_BEGIN_ALLOW_THREADS
    status=tomography::reslice(grid,m,o,h,v,n,first,last,top,end,threads,
                              static_cast<float *>(target.view.buf),planes==Py_None ? nullptr : pointers,fill);
    Py_END_ALLOW_THREADS
    if (status) { PyErr_SetString(PyExc_RuntimeError,"The compiled sampler could not complete its tile"); return nullptr; }
    Py_RETURN_NONE;
}

PyMethodDef methods[]={{"sample",sample,METH_VARARGS,"Sample one bounded full-quality row tile."},{nullptr,nullptr,0,nullptr}};
PyModuleDef module={PyModuleDef_HEAD_INIT,RESLICE_MODULE,"Full-quality CPU reslicing.",-1,methods};
}

PyMODINIT_FUNC RESLICE_INIT() {
    PyObject *result=PyModule_Create(&module);
    // ABI 2: 128-row tiles and up to 8 workers; an ABI 1 binary is never used with them.
    if (result && (PyModule_AddIntConstant(result,"ABI_VERSION",2)<0 ||
                   PyModule_AddIntConstant(result,"MAX_TILE_ROWS",MAX_TILE_ROWS)<0 ||
                   PyModule_AddIntConstant(result,"MAX_WORKERS",MAX_WORKERS)<0 ||
                   PyModule_AddIntConstant(result,"X86_64_V3",x86_64_v3())<0)) {
        Py_DECREF(result); return nullptr;
    }
    return result;
}
