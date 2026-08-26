import unittest
from pathlib import Path
from types import SimpleNamespace
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


class ToolchainManagementTests(unittest.TestCase):
    def test_github_release_lookup_prefers_authenticated_gh(self):
        response = '[{"tag_name": "v1.2.3", "draft": false, "prerelease": false}]'
        with (
            patch("setup_dev.shutil.which", return_value="gh"),
            patch.object(
                setup_dev,
                "capture_command",
                return_value=SimpleNamespace(returncode=0, stdout=response),
            ) as capture,
            patch.object(setup_dev, "fetch_text") as fetch,
        ):
            releases = setup_dev.github_release_items("example/project", 5)

        self.assertEqual(releases[0]["tag_name"], "v1.2.3")
        self.assertIn("per_page=5", capture.call_args.args[0][-1])
        fetch.assert_not_called()

    def test_interactive_plan_can_manage_multiple_toolchains_and_versions(self):
        selection = setup_dev.ToolchainSelection(compiler="clang", stdlib="libc++", linker="lld")
        answers = iter(("y", "2", "", "", "n"))

        def installed(toolchain, llvm_variant="auto"):
            return "13.2.0" if toolchain == "gcc" else None

        def releases(toolchain, **_kwargs):
            if toolchain == "gcc":
                return (
                    setup_dev.ToolchainRelease("16.2.0", "GCC 16.2.0", "test"),
                    setup_dev.ToolchainRelease("15.3.0", "GCC 15.3.0", "test"),
                )
            if toolchain == "llvm":
                return (
                    setup_dev.ToolchainRelease("22.1.8", "LLVM 22.1.8", "test"),
                    setup_dev.ToolchainRelease("21.1.8", "LLVM 21.1.8", "test"),
                )
            return ()

        with (
            patch.object(setup_dev, "installed_toolchain_version", side_effect=installed),
            patch.object(setup_dev, "available_toolchain_releases", side_effect=releases),
        ):
            requests = setup_dev.prompt_toolchain_install_requests(
                selection,
                system="Windows",
                input_fn=lambda _: next(answers),
            )

        self.assertEqual(
            requests,
            (
                setup_dev.ToolchainInstallRequest("gcc", "15.3.0", True, "auto"),
                setup_dev.ToolchainInstallRequest("llvm", "22.1.8", False, "mingw"),
            ),
        )

    def test_noninteractive_plan_requires_selected_version_to_have_install_target(self):
        selection = setup_dev.ToolchainSelection(compiler="gcc", stdlib="libstdc++", linker="bfd")
        with self.assertRaisesRegex(ValueError, "指定了版本但未"):
            setup_dev.noninteractive_toolchain_install_requests(
                selection,
                (),
                {"gcc": "16.2.0"},
                system="Windows",
            )

    def test_skipped_active_toolchain_is_not_implicitly_installed(self):
        selection = setup_dev.ToolchainSelection(compiler="gcc", stdlib="libstdc++", linker="bfd")
        with (
            patch.object(setup_dev, "is_windows", return_value=True),
            patch.object(setup_dev, "selected_compiler_executables", return_value=None),
            patch.object(setup_dev, "install_selected_compiler") as install,
        ):
            self.assertFalse(
                setup_dev.prepare_selected_toolchain(
                    selection,
                    False,
                    allow_compiler_install=False,
                )
            )

        install.assert_not_called()

    def test_upgrade_without_explicit_names_targets_all_required_clang_msvc_components(self):
        selection = setup_dev.ToolchainSelection(compiler="clang", stdlib="msvc", linker="lld")
        requests = setup_dev.noninteractive_toolchain_install_requests(
            selection,
            (),
            upgrade=True,
            system="Windows",
        )

        self.assertEqual([request.toolchain for request in requests], ["llvm", "msvc"])
        self.assertEqual(requests[0].llvm_variant, "msvc")

    def test_version_selector_accepts_latest_exact_and_major(self):
        releases = (
            setup_dev.ToolchainRelease("22.1.8", "LLVM 22", "test"),
            setup_dev.ToolchainRelease("21.1.8", "LLVM 21", "test"),
        )

        self.assertEqual(setup_dev.select_toolchain_release(releases, "latest").version, "22.1.8")
        self.assertEqual(setup_dev.select_toolchain_release(releases, "21.1.8").version, "21.1.8")
        self.assertEqual(setup_dev.select_toolchain_release(releases, "21").version, "21.1.8")
        self.assertIsNone(setup_dev.select_toolchain_release(releases, "20"))
        self.assertTrue(setup_dev.version_is_at_least("22.1.8", "22.1"))
        self.assertFalse(setup_dev.version_is_at_least("21.1.8", "22.1.0"))

    def test_winlibs_releases_use_github_asset_version_and_digest(self):
        data = [
            {
                "tag_name": "16.2.0posix-14.0.0-ucrt-r1",
                "assets": [
                    {
                        "name": "winlibs-x86_64-posix-seh-gcc-16.2.0-mingw-w64ucrt-14.0.0-r1.zip",
                        "browser_download_url": "https://example.invalid/gcc.zip",
                        "digest": "sha256:abc",
                    }
                ],
            }
        ]
        with (
            patch.object(setup_dev, "conan_host_arch", return_value="x86_64"),
            patch.object(setup_dev, "github_release_items", return_value=data),
        ):
            releases = setup_dev.winlibs_releases()

        self.assertEqual(releases[0].version, "16.2.0")
        self.assertEqual(releases[0].digest, "sha256:abc")

    def test_windows_gcc_install_uses_versioned_directory(self):
        release = setup_dev.ToolchainRelease(
            "16.2.0",
            "GCC 16.2.0",
            "test",
            "https://example.invalid/gcc.zip",
            "sha256:abc",
        )
        with (
            patch.object(setup_dev, "is_windows", return_value=True),
            patch.object(setup_dev, "installed_toolchain_version", return_value=None),
            patch.object(setup_dev, "requested_release", return_value=release),
            patch.object(setup_dev, "download_and_install_zip", return_value=True) as install,
            patch.object(setup_dev, "add_to_process_path"),
        ):
            self.assertTrue(setup_dev.install_gcc(False, version="16.2.0"))

        self.assertEqual(install.call_args.args[1], setup_dev.GCC_DIR / "16.2.0")
        self.assertEqual(install.call_args.kwargs["digest"], "sha256:abc")

    def test_msvc_selected_version_is_forwarded_to_winget(self):
        release = setup_dev.ToolchainRelease(
            "18.4.1",
            "Visual Studio Build Tools 2026 18.4.1",
            "WinGet",
            package_id="Microsoft.VisualStudio.BuildTools",
        )
        with (
            patch.object(setup_dev, "is_windows", return_value=True),
            patch.object(setup_dev, "installed_toolchain_version", return_value="17.14.0"),
            patch.object(setup_dev, "requested_release", return_value=release),
            patch("setup_dev.shutil.which", return_value="winget"),
            patch.object(setup_dev, "run_command", return_value=SimpleNamespace(returncode=0)) as run,
            patch.object(setup_dev, "has_msvc_build_environment", return_value=True),
        ):
            self.assertTrue(setup_dev.install_msvc(False, version="18.4.1", upgrade=True))

        command = run.call_args.args[0]
        self.assertEqual(command[1], "install")
        self.assertIn("18.4.1", command)

    def test_msvc_versions_fall_back_to_official_bootstrapper_channels(self):
        manifests = (
            '{"info": {"productDisplayVersion": "18.9.2"}}',
            '{"info": {"productDisplayVersion": "17.14.16"}}',
        )
        with (
            patch.object(setup_dev, "winget_package_versions", return_value=()),
            patch.object(setup_dev, "fetch_text", side_effect=manifests),
        ):
            releases = setup_dev.available_toolchain_releases("msvc", system="Windows")

        self.assertEqual([release.version for release in releases], ["18.9.2", "17.14.16"])
        self.assertTrue(releases[0].url.startswith("https://aka.ms/"))

    def test_homebrew_candidate_version_is_displayable_without_installing(self):
        response = '{"formulae": [{"versions": {"stable": "22.1.8"}}]}'
        with (
            patch("setup_dev.shutil.which", return_value="brew"),
            patch.object(
                setup_dev,
                "capture_command",
                return_value=SimpleNamespace(returncode=0, stdout=response),
            ),
        ):
            version = setup_dev.system_toolchain_candidate_version("llvm", "brew")

        self.assertEqual(version, "22.1.8")

    def test_microsoft_bootstrapper_requires_valid_publisher_signature(self):
        with (
            patch("setup_dev.shutil.which", return_value="powershell"),
            patch.object(
                setup_dev,
                "capture_command",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="Valid|CN=Microsoft Corporation, O=Microsoft Corporation",
                ),
            ),
        ):
            self.assertTrue(setup_dev.verify_microsoft_authenticode(Path("vs_buildtools.exe")))


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
