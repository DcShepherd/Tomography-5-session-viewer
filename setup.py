"""Optional CPU extensions; ordinary installations retain the NumPy path.

``_reslice`` is portable. On x86-64, ``_reslice_v3`` is the same source built for
x86-64-v3 (AVX2); the application loads it only where ``_reslice`` reports that
the CPU and operating system support it.
"""
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig

from setuptools import Extension, find_namespace_packages, setup
from setuptools.command.build_ext import build_ext


class BuildSampler(build_ext):
    def build_extensions(self):
        zig = os.environ.get("TOMOAPP_ZIG")
        if not zig:
            return super().build_extensions()
        # An explicit development toolchain; no downloads or runtime compilation.
        for extension in self.extensions:
            output = Path(self.get_ext_fullpath(extension.name)).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            command = [zig, "c++", "-std=c++17", "-O3", "-ffp-contract=off", "-fno-fast-math", "-shared",
                       "-I" + sysconfig.get_path("include"), *(f"-D{name}" for name, _ in extension.define_macros),
                       *extension.sources, "-o", str(output)]
            if extension.name.endswith("_v3"):
                command.append("-mcpu=x86_64_v3")
            if sys.platform == "win32":
                if not extension.name.endswith("_v3"):
                    command.append("-mcpu=x86_64")
                command.append(str(Path(sys.base_prefix) / "libs" / f"python{sys.version_info.major}{sys.version_info.minor}.lib"))
            elif sys.platform == "darwin":
                command += ["-undefined", "dynamic_lookup", "-fPIC"]
            else:
                command += ["-fPIC", "-pthread"]
            subprocess.run(command, check=True)


flags = ["/std:c++17", "/O2", "/fp:precise"] if sys.platform == "win32" else [
    "-std=c++17", "-O3", "-ffp-contract=off", "-fno-fast-math", "-pthread"]
packages = ["tomography_session_browser", *("tomography_session_browser." + name for name in
    find_namespace_packages("tomography_session_browser", exclude=["*__pycache__*", "native", "*.native", "assets", "assets.*"]))]
sources = ["tomography_session_browser/services/volume/native/kernel.cpp",
           "tomography_session_browser/services/volume/native/module.cpp"]
link = [] if sys.platform == "win32" else ["-pthread"]
extensions = [Extension(
    "tomography_session_browser.services.volume._reslice", sources=sources,
    depends=["tomography_session_browser/services/volume/native/kernel.hpp"],
    language="c++", extra_compile_args=flags, extra_link_args=link, optional=True)]
if platform.machine().lower() in ("amd64", "x86_64"):
    extensions.append(Extension(
        "tomography_session_browser.services.volume._reslice_v3", sources=sources,
        depends=["tomography_session_browser/services/volume/native/kernel.hpp"],
        define_macros=[("TOMOAPP_RESLICE_V3", None)], language="c++",
        extra_compile_args=flags + (["/arch:AVX2"] if sys.platform == "win32" else ["-march=x86-64-v3"]),
        extra_link_args=link, optional=True))
setup(packages=packages, ext_modules=extensions, cmdclass={"build_ext": BuildSampler})
