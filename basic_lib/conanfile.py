import os

from conan import ConanFile  # type: ignore
from conan.tools.cmake import CMakeDeps, CMakeToolchain, cmake_layout  # type: ignore


class BasicLibRecipe(ConanFile):
    name = "{{name}}"
    version = "{{version}}"
    package_type = "library"

    settings = "os", "compiler", "build_type", "arch"
    options = {
        "shared": [True, False],
        "fPIC": [True, False],
        "with_tests": [True, False],
        "with_examples": [True, False],
    }
    default_options = {
        "shared": False,
        "fPIC": True,
        "with_tests": False,
        "with_examples": False,
    }

    def config_options(self):
        if self.settings.os == "Windows":
            del self.options.fPIC

    def configure(self):
        if self.options.shared:
            self.options.rm_safe("fPIC")

    def layout(self):
        cmake_layout(self)

        compiler = str(self.settings.compiler).lower()
        libcxx = self.settings.get_safe("compiler.libcxx")

        comp_execs = self.conf.get(
            "tools.build:compiler_executables", default={}, check_type=dict
        )
        c_exec = str(comp_execs.get("c", "")).lower()
        cpp_exec = str(comp_execs.get("cpp", "")).lower()

        folder_name = "default"

        if compiler == "msvc":
            if (
                "llvm_msvc" in c_exec
                or "clang" in c_exec
                or "llvm_msvc" in cpp_exec
                or "clang" in cpp_exec
            ):
                folder_name = "clang_msvc"
            else:
                folder_name = "msvc"
        elif compiler == "gcc":
            folder_name = "gcc"
        elif compiler == "clang":
            if libcxx == "libc++":
                folder_name = "clang"
            else:
                folder_name = "clang_std"

        self.folders.generators = os.path.join(
            ".deps", folder_name, str(self.settings.build_type)
        )

    def requirements(self):
        # Add library dependencies here, for example:
        # self.requires("fmt/10.2.1")
        pass

    def generate(self):
        deps = CMakeDeps(self)
        deps.generate()

        toolchain = CMakeToolchain(self)
        toolchain.cache_variables["BUILD_TESTS"] = bool(self.options.with_tests)
        toolchain.cache_variables["BUILD_EXAMPLES"] = bool(self.options.with_examples)
        toolchain.generate()

    def build_requirements(self):
        if self.options.with_tests:
            self.test_requires("gtest/1.14.0")
