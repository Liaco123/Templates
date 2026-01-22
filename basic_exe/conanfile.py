import os

from conan import ConanFile  # type: ignore
from conan.tools.cmake import cmake_layout  # type: ignore


class MyProject(ConanFile):
    settings = "os", "compiler", "build_type", "arch"
    generators = "CMakeDeps", "CMakeToolchain"

    def layout(self):
        # 使用标准的 CMake layout 结构
        cmake_layout(self)

        # --- 核心路径映射逻辑 ---
        compiler = str(self.settings.compiler).lower()
        libcxx = self.settings.get_safe("compiler.libcxx")

        # 获取 Profile 中定义的编译器路径
        comp_execs = self.conf.get(
            "tools.build:compiler_executables", default={}, check_type=dict
        )
        c_exec = str(comp_execs.get("c", "")).lower()
        cpp_exec = str(comp_execs.get("cpp", "")).lower()

        folder_name = "unknown"

        # 1. 识别 MSVC 系列
        if compiler == "msvc":
            # 如果路径里包含 llvm_msvc 或 clang，说明是 Clang-CL
            if "llvm_msvc" in c_exec or "clang" in c_exec or "llvm_msvc" in cpp_exec:
                folder_name = "clang_msvc"
            else:
                folder_name = "msvc"

        # 2. 识别 GCC
        elif compiler == "gcc":
            folder_name = "gcc"

        # 3. 识别 Clang (MinGW/LLVM 系列)
        elif compiler == "clang":
            if libcxx == "libc++":
                folder_name = "clang"  # 对应 clang+libc++
            else:
                folder_name = "clang_std"  # 对应 clang+libstdc++

        self.folders.generators = os.path.join(
            ".deps", folder_name, str(self.settings.build_type)
        )

    def requirements(self):
        pass
