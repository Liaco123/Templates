import unittest
from pathlib import Path
from unittest.mock import patch

import setup_dev
import install_uv_tools


class FindExecutableTests(unittest.TestCase):
    def test_project_environment_takes_priority_over_legacy_global_wrapper(self):
        project_bin = Path("project-bin")
        project_executable = project_bin / "conan"

        with (
            patch.object(setup_dev, "is_windows", return_value=False),
            patch.object(setup_dev, "PROJECT_VENV_BIN", project_bin),
            patch.object(setup_dev, "LOCAL_BIN", Path("legacy-bin")),
            patch.object(Path, "exists", autospec=True, side_effect=lambda path: path == project_executable),
            patch("setup_dev.shutil.which", return_value=str(Path("path-bin") / "conan")),
        ):
            self.assertEqual(setup_dev.find_executable("conan"), str(project_executable))

    def test_clang_abi_detection_uses_reported_target(self):
        with patch.object(
            setup_dev,
            "compiler_version_text",
            side_effect=(
                "clang version 22 target: x86_64-pc-windows-msvc",
                "clang version 22 target: x86_64-w64-windows-gnu",
            ),
        ):
            self.assertTrue(setup_dev.is_msvc_abi_clang(Path("clang-cl.exe")))
            self.assertFalse(setup_dev.is_msvc_abi_clang(Path("clang++.exe")))


class LockedGlobalToolsTests(unittest.TestCase):
    def test_global_tool_requirements_come_from_uv_lock(self):
        self.assertEqual(
            install_uv_tools.locked_tool_requirements(),
            {
                "conan": "conan==2.30.0",
                "cmake": "cmake==4.3.4",
                "ninja": "ninja==1.13.0",
                "ruff": "ruff==0.15.21",
            },
        )


class ToolchainSelectionTests(unittest.TestCase):
    def test_linux_clang_interactive_selection(self):
        answers = iter(("1", "2", "3"))

        with patch.object(setup_dev, "default_compiler", return_value="gcc"):
            selection = setup_dev.resolve_toolchain_selection(
                interactive=True,
                system="Linux",
                input_fn=lambda _: next(answers),
            )

        self.assertEqual(
            selection,
            setup_dev.ToolchainSelection(compiler="clang", stdlib="libstdc++", linker="lld"),
        )

    def test_windows_msvc_rejects_libcxx(self):
        with self.assertRaisesRegex(ValueError, "不支持 libc\\+\\+"):
            setup_dev.resolve_toolchain_selection(
                compiler="msvc",
                stdlib="libc++",
                linker="msvc",
                system="Windows",
            )

    def test_windows_clang_with_msvc_stl_uses_clang_cl_mode(self):
        selection = setup_dev.ToolchainSelection(compiler="clang", stdlib="msvc", linker="lld")

        with patch.object(setup_dev, "is_windows", return_value=True):
            self.assertEqual(setup_dev.selection_compiler_mode(selection), "clang_msvc")
            self.assertEqual(setup_dev.preset_group_for_selection(selection), "clang-msvc")

    def test_platform_compatibility_matrix(self):
        self.assertEqual(setup_dev.supported_compilers("Windows"), ("clang", "gcc", "msvc"))
        self.assertEqual(setup_dev.supported_standard_libraries("clang", "Linux"), ("libc++", "libstdc++"))
        self.assertEqual(setup_dev.supported_linkers("gcc", "libstdc++", "Linux"), ("system", "bfd", "lld", "mold"))
        self.assertEqual(setup_dev.conan_host_arch("AMD64"), "x86_64")
        self.assertEqual(setup_dev.conan_host_arch("aarch64"), "armv8")


class PackageManagerTests(unittest.TestCase):
    def test_missing_winget_tool_is_checked_once(self):
        tool = setup_dev.Tool("cppcheck", "checker", False, windows_winget_id="Cppcheck.Cppcheck")
        with (
            patch.object(setup_dev, "find_executable", return_value=None),
            patch.object(setup_dev, "install_with_winget", return_value=False) as install,
        ):
            self.assertFalse(setup_dev.ensure_tool(tool, uv=None, check_only=True))

        install.assert_called_once_with(tool, True)

    def test_package_names_are_selected_by_package_manager(self):
        self.assertEqual(setup_dev.packages_for("gcc", "apt-get"), ("gcc", "g++"))
        self.assertEqual(setup_dev.packages_for("libc++", "dnf"), ("libcxx-devel", "libcxxabi-devel"))
        self.assertEqual(setup_dev.packages_for("lld", "pacman"), ("lld",))
        self.assertEqual(setup_dev.packages_for("gcc", "zypper"), ("gcc", "gcc-c++"))

    def test_selected_packages_are_merged_without_duplicates(self):
        selection = setup_dev.ToolchainSelection(compiler="clang", stdlib="libc++", linker="lld")

        packages = setup_dev.planned_system_packages(
            selection,
            "apt-get",
            compiler_missing=True,
            stdlib_missing=True,
            linker_missing=True,
        )

        self.assertEqual(packages, ("clang", "libc++-dev", "libc++abi-dev", "lld"))

    def test_latest_llvm_windows_installer_uses_official_release_asset(self):
        release = (
            '{"assets": ['
            '{"name": "LLVM-22.1.8-win64.exe.sig", "browser_download_url": "signature"},'
            '{"name": "LLVM-22.1.8-win64.exe", "browser_download_url": "https://example.invalid/LLVM.exe"}'
            "]}"
        )

        with patch.object(setup_dev, "fetch_text", return_value=release):
            self.assertEqual(setup_dev.latest_llvm_msvc_url(), "https://example.invalid/LLVM.exe")


class ConanProfileTests(unittest.TestCase):
    def test_profile_records_selected_linker(self):
        profiles_dir = Path("profiles")
        with (
            patch.object(setup_dev, "conan_profiles_dir", return_value=profiles_dir),
            patch.object(Path, "mkdir", autospec=True),
            patch.object(Path, "write_text", autospec=True) as write_text,
        ):
            setup_dev.write_profile(
                "clang",
                {
                    "os": "Linux",
                    "arch": "x86_64",
                    "compiler": "clang",
                    "compiler.version": "20",
                    "compiler.cppstd": "23",
                    "compiler.libcxx": "libc++",
                    "build_type": "Release",
                },
                {"c": "/usr/bin/clang", "cpp": "/usr/bin/clang++"},
                "lld",
                Path("/opt/llvm/bin/ld.lld"),
            )

        written_path, content = write_text.call_args.args[:2]

        self.assertEqual(written_path, profiles_dir / "clang")
        self.assertIn("compiler.libcxx=libc++", content)
        self.assertIn("'CMAKE_LINKER_TYPE': {'value': 'LLD'", content)

        from conan.internal.api.profile.profile_loader import _ProfileValueParser

        profile = _ProfileValueParser.get_profile(content)
        extra_variables = profile.conf.get("tools.cmake.cmaketoolchain:extra_variables", check_type=dict)
        self.assertEqual(extra_variables["CMAKE_LINKER_TYPE"]["value"], "LLD")
        self.assertIn(str(Path("/opt/llvm/bin")), profile.buildenv.dumps())


if __name__ == "__main__":
    unittest.main()
