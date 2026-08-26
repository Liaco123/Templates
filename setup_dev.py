import argparse
import gzip
import hashlib
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import install_uv_tools


HOME = Path.home()
CONAN_HOME = Path(os.environ.get("CONAN_HOME", HOME / ".conan2")).expanduser()
PROJECT_ROOT = Path(__file__).resolve().parent
PROJECT_VENV_BIN = PROJECT_ROOT / ".venv" / ("Scripts" if platform.system().lower() == "windows" else "bin")
LOCAL_BIN = HOME / ".local" / "bin"
UV_INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "uv" / "bin"
DEV_ROOT = Path("C:/dev") if platform.system().lower() == "windows" else HOME / "dev"
GCC_DIR = DEV_ROOT / "gcc"
CLANG_DIR = DEV_ROOT / "llvm-mingw"
CLANG_MSVC_DIR = DEV_ROOT / "clang_msvc"
APT_LLVM_BASE_URL = "https://apt.llvm.org"
APT_LLVM_KEY_URL = f"{APT_LLVM_BASE_URL}/llvm-snapshot.gpg.key"
APT_LLVM_KEY_FINGERPRINT = "6084F3CF814B57C1CF12EFD515CF4D18AF4F7421"
APT_LLVM_PROVIDER = "apt.llvm.org"
SYSTEM_PACKAGE_PROVIDER = "system-package-manager"
CONAN_CPPSTD = "23"
CONAN_CMAKE_GENERATOR = "Ninja"
DEFAULT_MSVC_VERSION = "193"
MANAGED_PROFILE_NAMES = {"gcc", "clang", "msvc", "clang_msvc"}

COMPILER_LABELS = {
    "clang": "Clang/LLVM",
    "gcc": "GCC",
    "msvc": "Microsoft MSVC",
}
TOOLCHAIN_LABELS = {
    "gcc": "GCC",
    "llvm": "LLVM/Clang",
    "msvc": "Microsoft MSVC Build Tools",
}
LLVM_VARIANT_LABELS = {
    "mingw": "LLVM/MinGW（clang++ + libc++）",
    "msvc": "LLVM/MSVC（clang-cl + Microsoft STL）",
}
MSVC_WINGET_PACKAGES = (
    ("Visual Studio 2026 Build Tools", "Microsoft.VisualStudio.BuildTools"),
    ("Visual Studio 2022 Build Tools", "Microsoft.VisualStudio.2022.BuildTools"),
)
MSVC_BOOTSTRAPPERS = (
    (
        "18",
        "Visual Studio 2026 Build Tools（Stable 通道最新服务版本）",
        "https://aka.ms/vs/stable/vs_buildtools.exe",
        "https://aka.ms/vs/stable/channel",
    ),
    (
        "17",
        "Visual Studio 2022 Build Tools（Current 通道最新服务版本）",
        "https://aka.ms/vs/17/release/vs_buildtools.exe",
        "https://aka.ms/vs/17/release/channel",
    ),
)
STDLIB_LABELS = {
    "libc++": "LLVM libc++",
    "libstdc++": "GNU libstdc++",
    "msvc": "Microsoft C++ Standard Library",
}
LINKER_LABELS = {
    "system": "系统默认链接器",
    "lld": "LLVM lld",
    "bfd": "GNU ld.bfd",
    "mold": "mold",
    "msvc": "Microsoft link.exe",
}
CMAKE_LINKER_TYPES = {
    "system": "SYSTEM",
    "lld": "LLD",
    "bfd": "BFD",
    "mold": "MOLD",
    "msvc": "MSVC",
}


LAST_AVAILABLE_PRESET_GROUPS: set[str] = set()
LAST_PRESET_ENVIRONMENTS: dict[str, dict[str, str]] = {}
LAST_TOOLCHAIN_SELECTION = None
PREFERRED_TOOLCHAIN_BINS: dict[str, Path] = {}
PREFERRED_COMPILER_PAIRS: dict[str, tuple[Path, Path]] = {}
PREFERRED_LINKERS: dict[str, Path] = {}


@dataclass(frozen=True)
class Tool:
    command: str
    description: str
    required: bool
    uv_package: str | None = None
    windows_winget_id: str | None = None
    manual_hint: str | None = None


@dataclass(frozen=True)
class ToolchainSelection:
    compiler: str
    stdlib: str
    linker: str


@dataclass(frozen=True)
class ToolchainRelease:
    version: str
    label: str
    provider: str
    url: str | None = None
    digest: str | None = None
    package_id: str | None = None


@dataclass(frozen=True)
class ToolchainInstallRequest:
    toolchain: str
    version: str = "latest"
    upgrade: bool = False
    llvm_variant: str = "auto"
    stdlib: str = "auto"
    linker: str = "auto"


TOOLS = [
    Tool(
        "git", "版本控制工具", True, windows_winget_id="Git.Git", manual_hint="安装 Git: https://git-scm.com/downloads"
    ),
    Tool("conan", "C/C++ 包管理器", True, uv_package="conan", manual_hint="安装 uv 后运行: uv tool install conan"),
    Tool("cmake", "CMake 构建系统", True, uv_package="cmake", manual_hint="安装 uv 后运行: uv tool install cmake"),
    Tool("ninja", "Ninja 构建器", True, uv_package="ninja", manual_hint="安装 uv 后运行: uv tool install ninja"),
    Tool("ruff", "Python 代码检查工具", False, uv_package="ruff", manual_hint="安装 uv 后运行: uv tool install ruff"),
    Tool(
        "cppcheck",
        "C/C++ 静态分析工具",
        False,
        windows_winget_id="Cppcheck.Cppcheck",
        manual_hint="Windows 可安装 Cppcheck；Linux/macOS 使用系统包管理器安装 cppcheck",
    ),
    Tool("sccache", "编译缓存工具", False, manual_hint="安装 sccache: https://github.com/mozilla/sccache/releases"),
]


def compiler_install_notes() -> dict[str, str]:
    if is_windows():
        return {
            "gcc": f"自动安装来源：https://winlibs.com/；目标目录：{GCC_DIR}",
            "clang": f"自动安装来源：https://github.com/mstorsjo/llvm-mingw/releases；目标目录：{CLANG_DIR}",
            "clang_msvc": f"自动安装来源：https://github.com/llvm/llvm-project/releases；目标目录：{CLANG_MSVC_DIR}",
            "msvc": "通过 WinGet 安装 Visual Studio Build Tools 的 C++ workload。",
        }
    if platform.system().lower() == "darwin":
        return {
            "gcc": "如已安装 Homebrew，可自动执行 brew install gcc；否则请先安装 Homebrew。",
            "clang": "通常由 Xcode Command Line Tools 提供；如缺失，请手动运行 xcode-select --install。",
            "msvc": "MSVC 仅支持 Windows。",
        }
    return {
        "gcc": "Linux 下优先使用 apt/dnf/pacman 安装 gcc/g++。",
        "clang": "Linux 下优先使用 apt/dnf/pacman 安装 clang。",
        "clang_msvc": "clang_msvc 只在 Windows/MSVC 工具链下生成。",
        "msvc": "MSVC 仅支持 Windows。",
    }


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def normalized_system(system: str | None = None) -> str:
    value = (system or platform.system()).lower()
    if value == "darwin":
        return "darwin"
    if value == "windows":
        return "windows"
    return "linux"


def conan_host_arch(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower()
    if value in {"amd64", "x86_64"}:
        return "x86_64"
    if value in {"x86", "i386", "i486", "i586", "i686"}:
        return "x86"
    if value in {"arm64", "aarch64"}:
        return "armv8"
    if value.startswith("armv7"):
        return "armv7"
    return value


def supported_compilers(system: str | None = None) -> tuple[str, ...]:
    if normalized_system(system) == "windows":
        return ("clang", "gcc", "msvc")
    return ("clang", "gcc")


def supported_standard_libraries(compiler: str, system: str | None = None) -> tuple[str, ...]:
    host = normalized_system(system)
    if compiler == "msvc":
        return ("msvc",)
    if compiler == "gcc":
        return ("libstdc++",)
    if host == "windows":
        # llvm-mingw provides libc++; clang-cl uses the Microsoft ABI/STL.
        return ("libc++", "msvc")
    if host == "darwin":
        return ("libc++",)
    return ("libc++", "libstdc++")


def supported_linkers(compiler: str, stdlib: str, system: str | None = None) -> tuple[str, ...]:
    host = normalized_system(system)
    if host == "windows":
        if compiler == "msvc" or (compiler == "clang" and stdlib == "msvc"):
            return ("msvc", "lld")
        if compiler == "gcc":
            return ("bfd", "lld")
        return ("lld",)
    if host == "darwin":
        return ("system", "lld")
    return ("system", "bfd", "lld", "mold")


def default_compiler(system: str | None = None) -> str:
    host = normalized_system(system)
    discovered = discovered_compilers()
    preference = {
        "windows": ("msvc", "clang", "gcc"),
        "darwin": ("clang", "gcc"),
        "linux": ("gcc", "clang"),
    }[host]
    for compiler in preference:
        if compiler == "clang" and ("clang" in discovered or "clang_msvc" in discovered):
            return compiler
        if compiler in discovered:
            return compiler
    return preference[0]


def default_standard_library(compiler: str, system: str | None = None) -> str:
    choices = supported_standard_libraries(compiler, system)
    host = normalized_system(system)
    if compiler == "clang" and host == "windows":
        discovered = discovered_compilers()
        if "clang_msvc" in discovered and "clang" not in discovered:
            return "msvc"
    if compiler == "clang" and host == "linux" and "libstdc++" in choices:
        return "libstdc++"
    return choices[0]


def default_linker(compiler: str, stdlib: str, system: str | None = None) -> str:
    choices = supported_linkers(compiler, stdlib, system)
    preferred = "msvc" if normalized_system(system) == "windows" and "msvc" in choices else choices[0]
    return preferred


def prompt_choice(title: str, choices: tuple[str, ...], labels: dict[str, str], default: str, input_fn=input) -> str:
    print(f"\n[选择] {title}")
    for index, choice in enumerate(choices, start=1):
        suffix = "（默认）" if choice == default else ""
        print(f"  {index}. {labels[choice]} [{choice}] {suffix}")

    while True:
        raw = input_fn(f"请输入编号或名称，直接回车使用 {default}：").strip().lower()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        if raw in choices:
            return raw
        warn(f"无效选择：{raw or '<空>'}。可选值：{', '.join(choices)}")


def prompt_yes_no(question: str, *, default: bool = False, input_fn=input) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input_fn(f"{question} {suffix}：").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "是"}:
            return True
        if raw in {"n", "no", "否"}:
            return False
        warn("请输入 y/yes 或 n/no。")


def supported_install_toolchains(system: str | None = None) -> tuple[str, ...]:
    if normalized_system(system) == "windows":
        return ("gcc", "llvm", "msvc")
    return ("gcc", "llvm")


def active_install_toolchain(selection: ToolchainSelection) -> str:
    return "llvm" if selection.compiler == "clang" else selection.compiler


def required_install_toolchains(selection: ToolchainSelection, system: str | None = None) -> tuple[str, ...]:
    active = active_install_toolchain(selection)
    if active == "llvm" and llvm_variant_for_selection(selection, system) == "msvc":
        return ("llvm", "msvc")
    return (active,)


def llvm_variant_for_selection(selection: ToolchainSelection, system: str | None = None) -> str:
    if normalized_system(system) != "windows":
        return "auto"
    if selection.compiler == "clang" and selection.stdlib == "msvc":
        return "msvc"
    return "mingw"


def resolve_toolchain_selection(
    compiler: str = "auto",
    stdlib: str = "auto",
    linker: str = "auto",
    *,
    interactive: bool = False,
    system: str | None = None,
    input_fn=input,
) -> ToolchainSelection:
    system_name = system or platform.system()
    compiler_choices = supported_compilers(system)
    if compiler == "auto":
        compiler_default = default_compiler(system)
        compiler = (
            prompt_choice("C/C++ 编译器", compiler_choices, COMPILER_LABELS, compiler_default, input_fn)
            if interactive
            else compiler_default
        )
    if compiler not in compiler_choices:
        raise ValueError(f"{system_name} 不支持编译器选择 {compiler}；可选值：{', '.join(compiler_choices)}")

    stdlib_choices = supported_standard_libraries(compiler, system)
    if stdlib == "auto":
        stdlib_default = default_standard_library(compiler, system)
        stdlib = (
            prompt_choice("C++ 标准库", stdlib_choices, STDLIB_LABELS, stdlib_default, input_fn)
            if interactive and len(stdlib_choices) > 1
            else stdlib_default
        )
    if stdlib not in stdlib_choices:
        raise ValueError(
            f"{COMPILER_LABELS[compiler]} 在 {system_name} 上不支持 {stdlib}；可选值：{', '.join(stdlib_choices)}"
        )

    linker_choices = supported_linkers(compiler, stdlib, system)
    if linker == "auto":
        linker_default = default_linker(compiler, stdlib, system)
        linker = (
            prompt_choice("链接器", linker_choices, LINKER_LABELS, linker_default, input_fn)
            if interactive and len(linker_choices) > 1
            else linker_default
        )
    if linker not in linker_choices:
        raise ValueError(
            f"{COMPILER_LABELS[compiler]} + {STDLIB_LABELS[stdlib]} 在 {system_name} 上不支持链接器 {linker}；"
            f"可选值：{', '.join(linker_choices)}"
        )

    selection = ToolchainSelection(compiler=compiler, stdlib=stdlib, linker=linker)
    log(
        "已选择工具链："
        f"{COMPILER_LABELS[selection.compiler]} + {STDLIB_LABELS[selection.stdlib]} + {LINKER_LABELS[selection.linker]}"
    )
    return selection


def selection_compiler_mode(selection: ToolchainSelection) -> str:
    if is_windows() and selection.compiler == "clang" and selection.stdlib == "msvc":
        return "clang_msvc"
    return selection.compiler


def log(message: str) -> None:
    print(f"[信息] {message}")


def ok(message: str) -> None:
    print(f"[完成] {message}")


def warn(message: str) -> None:
    print(f"[警告] {message}")


def error(message: str) -> None:
    print(f"[错误] {message}")


def run_command(command: list[str] | str, *, shell: bool = False) -> subprocess.CompletedProcess:
    rendered = command if isinstance(command, str) else " ".join(command)
    log(f"执行命令：{rendered}")
    return subprocess.run(command, shell=shell, text=True)


def capture_command(command: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def download_file(url: str, destination: Path) -> bool:
    log(f"下载文件：{url}")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "templates-setup"})
        with urllib.request.urlopen(request, timeout=60) as response:
            with destination.open("wb") as output:
                shutil.copyfileobj(response, output)
    except OSError as exc:
        warn(f"下载失败：{exc}")
        return False
    ok(f"下载完成：{destination}")
    return True


def verify_file_digest(path: Path, digest: str | None) -> bool:
    if not digest:
        warn(f"下载源没有提供校验摘要：{path.name}")
        return True
    algorithm, separator, expected = digest.partition(":")
    if separator != ":" or algorithm.lower() != "sha256" or not expected:
        warn(f"不支持的下载摘要格式：{digest}")
        return False
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
    except OSError as exc:
        warn(f"读取下载文件进行校验失败：{exc}")
        return False
    if hasher.hexdigest().lower() != expected.lower():
        warn(f"SHA-256 校验失败：{path.name}")
        return False
    ok(f"SHA-256 校验通过：{path.name}")
    return True


def verify_microsoft_authenticode(path: Path) -> bool:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        warn("无法找到 PowerShell，不能验证 Microsoft 安装器签名。")
        return False
    script = (
        "$signature = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        'Write-Output "$($signature.Status)|$($signature.SignerCertificate.Subject)"'
    )
    try:
        result = capture_command([powershell, "-NoProfile", "-Command", script, str(path)], timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"验证 Microsoft 安装器签名失败：{exc}")
        return False
    output = result.stdout.strip()
    if result.returncode == 0 and output.startswith("Valid|") and "Microsoft Corporation" in output:
        ok(f"Microsoft Authenticode 签名校验通过：{path.name}")
        return True
    warn(f"Microsoft Authenticode 签名无效：{output or '无签名信息'}")
    return False


def fetch_text(url: str) -> str | None:
    log(f"读取下载页面：{url}")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "templates-setup"})
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            content_encoding = response.headers.get("Content-Encoding", "").lower()
            if content_encoding == "gzip" or content.startswith(b"\x1f\x8b"):
                content = gzip.decompress(content)
            return content.decode("utf-8", errors="replace")
    except (OSError, EOFError) as exc:
        warn(f"读取页面失败：{exc}")
        return None


def semantic_version_key(value: str) -> tuple[int, ...]:
    matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)*)(?!\d)", value)
    if not matches:
        return ()
    version = max(matches, key=lambda match: (match.count("."), len(match)))
    return tuple(int(part) for part in version.split("."))


def version_is_at_least(installed: str, target: str) -> bool:
    installed_key = semantic_version_key(installed)
    target_key = semantic_version_key(target)
    if not installed_key or not target_key:
        return False
    length = max(len(installed_key), len(target_key))
    return installed_key + (0,) * (length - len(installed_key)) >= target_key + (0,) * (length - len(target_key))


def github_release_items(repository: str, limit: int = 20) -> list[dict]:
    text = None
    gh = shutil.which("gh")
    if gh:
        try:
            result = capture_command([gh, "api", f"repos/{repository}/releases?per_page={limit}"], timeout=60)
        except (OSError, subprocess.SubprocessError):
            result = None
        if result and result.returncode == 0:
            log(f"通过 gh 读取 {repository} release 列表。")
            text = result.stdout
    if not text:
        text = fetch_text(f"https://api.github.com/repos/{repository}/releases?per_page={limit}")
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        warn(f"解析 {repository} GitHub release JSON 失败。")
        return []
    if not isinstance(data, list):
        warn(f"{repository} GitHub release 响应格式无效。")
        return []
    return [item for item in data if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease")]


def release_asset(release: dict, predicate) -> dict | None:
    for asset in release.get("assets", []):
        if isinstance(asset, dict) and predicate(asset):
            return asset
    return None


def release_from_asset(version: str, label: str, provider: str, asset: dict) -> ToolchainRelease:
    return ToolchainRelease(
        version=version,
        label=label,
        provider=provider,
        url=asset.get("browser_download_url") or None,
        digest=asset.get("digest") or None,
    )


def unique_sorted_releases(releases: list[ToolchainRelease]) -> tuple[ToolchainRelease, ...]:
    ordered = sorted(releases, key=lambda item: semantic_version_key(item.version), reverse=True)
    unique: dict[str, ToolchainRelease] = {}
    for release in ordered:
        unique.setdefault(release.version, release)
    return tuple(unique.values())


def winlibs_releases(limit: int = 12) -> tuple[ToolchainRelease, ...]:
    arch = conan_host_arch()
    asset_arch = "i686" if arch == "x86" else "x86_64" if arch == "x86_64" else ""
    if not asset_arch:
        warn(f"WinLibs 暂不支持当前架构：{arch}")
        return ()

    releases: list[ToolchainRelease] = []
    for release in github_release_items("brechtsanders/winlibs_mingw", limit):
        asset = release_asset(
            release,
            lambda item: (
                item.get("name", "").lower().startswith(f"winlibs-{asset_arch}-posix-")
                and item.get("name", "").lower().endswith(".zip")
            ),
        )
        if not asset:
            continue
        match = re.search(r"gcc-(\d+(?:\.\d+)+)-", asset.get("name", ""), re.IGNORECASE)
        if not match:
            continue
        version = match.group(1)
        releases.append(
            release_from_asset(
                version,
                f"GCC {version}（WinLibs / MinGW-w64 UCRT）",
                "WinLibs GitHub Releases",
                asset,
            )
        )
    return unique_sorted_releases(releases)


def llvm_mingw_releases(limit: int = 12) -> tuple[ToolchainRelease, ...]:
    arch = conan_host_arch()
    asset_arch = {"x86_64": "x86_64", "x86": "i686", "armv8": "aarch64", "armv7": "armv7"}.get(arch)
    if not asset_arch:
        warn(f"llvm-mingw 暂不支持当前架构：{arch}")
        return ()

    releases: list[ToolchainRelease] = []
    for release in github_release_items("mstorsjo/llvm-mingw", limit):
        description = f"{release.get('name', '')} {release.get('tag_name', '')}"
        match = re.search(r"LLVM\s+(\d+(?:\.\d+)+)", description, re.IGNORECASE)
        if not match:
            continue
        version = match.group(1)
        suffix = f"ucrt-{asset_arch}.zip"
        asset = release_asset(release, lambda item: item.get("name", "").lower().endswith(suffix))
        if not asset:
            continue
        release_tag = release.get("tag_name", "")
        releases.append(
            release_from_asset(
                version,
                f"LLVM {version}（llvm-mingw {release_tag}）",
                "mstorsjo/llvm-mingw GitHub Releases",
                asset,
            )
        )
    return unique_sorted_releases(releases)


def llvm_msvc_releases(limit: int = 12) -> tuple[ToolchainRelease, ...]:
    arch = conan_host_arch()
    suffix = "-woa64.exe" if arch == "armv8" else "-win64.exe" if arch == "x86_64" else ""
    if not suffix:
        warn(f"LLVM Windows 官方安装器暂不支持当前架构：{arch}")
        return ()

    releases: list[ToolchainRelease] = []
    for release in github_release_items("llvm/llvm-project", limit):
        match = re.search(r"llvmorg-(\d+(?:\.\d+)+)", release.get("tag_name", ""), re.IGNORECASE)
        if not match:
            continue
        version = match.group(1)
        asset = release_asset(
            release,
            lambda item: item.get("name", "").lower() == f"llvm-{version}{suffix}".lower(),
        )
        if not asset:
            continue
        releases.append(
            release_from_asset(
                version,
                f"LLVM {version}（官方 Windows MSVC 工具链）",
                "llvm/llvm-project GitHub Releases",
                asset,
            )
        )
    return unique_sorted_releases(releases)


def winget_package_versions(package_id: str, label: str, limit: int = 8) -> tuple[ToolchainRelease, ...]:
    winget = shutil.which("winget")
    if not winget:
        return ()
    try:
        result = capture_command(
            [
                winget,
                "show",
                "--id",
                package_id,
                "-e",
                "--source",
                "winget",
                "--versions",
                "--accept-source-agreements",
            ],
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()

    versions = {line.strip() for line in result.stdout.splitlines() if re.fullmatch(r"\d+(?:\.\d+){1,3}", line.strip())}
    ordered = sorted(versions, key=semantic_version_key, reverse=True)[:limit]
    return tuple(
        ToolchainRelease(
            version=version,
            label=f"{label} {version}",
            provider="WinGet",
            package_id=package_id,
        )
        for version in ordered
    )


def msvc_bootstrapper_releases() -> tuple[ToolchainRelease, ...]:
    releases: list[ToolchainRelease] = []
    for major, label, installer_url, channel_url in MSVC_BOOTSTRAPPERS:
        version = major
        text = fetch_text(channel_url)
        if text:
            try:
                data = json.loads(text)
                display_version = str(data.get("info", {}).get("productDisplayVersion", ""))
                match = re.search(r"\d+(?:\.\d+)+", display_version)
                if match and match.group(0).startswith(f"{major}."):
                    version = match.group(0)
            except json.JSONDecodeError:
                warn(f"解析 Visual Studio {major} channel manifest 失败。")
        releases.append(
            ToolchainRelease(
                version=version,
                label=f"{label}：{version}",
                provider="Microsoft Visual Studio evergreen bootstrapper",
                url=installer_url,
            )
        )
    return tuple(releases)


def system_toolchain_candidate_version(toolchain: str, manager: str) -> str | None:
    package = "gcc" if toolchain == "gcc" else "llvm" if manager == "brew" else "clang"
    try:
        if manager == "brew":
            result = capture_command([shutil.which("brew") or "brew", "info", "--json=v2", package], timeout=60)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                formulae = data.get("formulae", [])
                stable = formulae[0].get("versions", {}).get("stable") if formulae else None
                return str(stable) if stable else None
        elif manager == "apt-get":
            apt_cache = shutil.which("apt-cache") or "apt-cache"
            result = capture_command([apt_cache, "policy", "gcc" if toolchain == "gcc" else "clang"])
            match = re.search(r"^\s*Candidate:\s*(\S+)", result.stdout, re.MULTILINE | re.IGNORECASE)
            return match.group(1) if result.returncode == 0 and match else None
        elif manager == "dnf":
            result = capture_command(
                [manager, "repoquery", "--latest-limit", "1", "--qf", "%{version}", package],
                timeout=60,
            )
            versions = [line.strip() for line in result.stdout.splitlines() if semantic_version_key(line.strip())]
            return versions[-1] if result.returncode == 0 and versions else None
        elif manager == "pacman":
            result = capture_command([manager, "-Sp", "--print-format", "%v", package], timeout=60)
            versions = [line.strip() for line in result.stdout.splitlines() if semantic_version_key(line.strip())]
            return versions[-1] if result.returncode == 0 and versions else None
        elif manager == "zypper":
            result = capture_command([manager, "--non-interactive", "info", package], timeout=60)
            match = re.search(r"^\s*Version\s*:\s*(\S+)", result.stdout, re.MULTILINE | re.IGNORECASE)
            return match.group(1) if result.returncode == 0 and match else None
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError):
        return None
    return None


def apt_package_candidate(package: str) -> str | None:
    apt_cache = shutil.which("apt-cache") or "apt-cache"
    try:
        result = capture_command([apt_cache, "policy", package])
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"^\s*Candidate:\s*(\S+)", result.stdout, re.MULTILINE | re.IGNORECASE)
    if result.returncode != 0 or not match or match.group(1) == "(none)":
        return None
    return match.group(1)


def apt_versioned_toolchain_releases(toolchain: str) -> tuple[ToolchainRelease, ...]:
    apt_cache = shutil.which("apt-cache") or "apt-cache"
    try:
        result = capture_command([apt_cache, "pkgnames"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()

    package_names = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    if toolchain == "gcc":
        gcc_majors = {name.removeprefix("gcc-") for name in package_names if re.fullmatch(r"gcc-\d+", name)}
        gxx_majors = {name.removeprefix("g++-") for name in package_names if re.fullmatch(r"g\+\+-\d+", name)}
        majors = gcc_majors & gxx_majors
        package_prefix = "gcc"
        label = "GCC"
    elif toolchain == "llvm":
        majors = {name.removeprefix("clang-") for name in package_names if re.fullmatch(r"clang-\d+", name)}
        package_prefix = "clang"
        label = "LLVM/Clang"
    else:
        return ()

    releases: list[ToolchainRelease] = []
    for major in sorted(majors, key=semantic_version_key, reverse=True):
        package = f"{package_prefix}-{major}"
        candidate = apt_package_candidate(package)
        if not candidate:
            continue
        releases.append(
            ToolchainRelease(
                version=major,
                label=f"{label} {major}（APT 候选包 {candidate}）",
                provider=SYSTEM_PACKAGE_PROVIDER,
                package_id=package,
            )
        )
    return tuple(releases)


def linux_distribution_codename() -> str | None:
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        return None
    return release.get("VERSION_CODENAME") or release.get("UBUNTU_CODENAME") or None


def apt_llvm_releases(page: str | None = None, codename: str | None = None) -> tuple[ToolchainRelease, ...]:
    codename = codename or linux_distribution_codename()
    if not codename:
        return ()
    page = page if page is not None else fetch_text(f"{APT_LLVM_BASE_URL}/")
    if not page:
        return ()

    plain_text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    plain_text = re.sub(r"\s+", " ", plain_text)
    suite_pattern = rf"llvm-toolchain-{re.escape(codename)}(?:-(\d+))?\s+main"
    suite_matches = re.findall(suite_pattern, plain_text, re.IGNORECASE)
    if not suite_matches:
        return ()

    channel_patterns = {
        "稳定分支": r"Install\s+\(stable branch\).*?apt-get install clang-(\d+)",
        "资格分支": r"Install\s+\(qualification branch\).*?apt-get install clang-(\d+)",
        "开发分支": r"Install\s+\(development branch\).*?apt-get install clang-(\d+)",
    }
    channels: dict[str, str] = {}
    for channel, pattern in channel_patterns.items():
        match = re.search(pattern, plain_text, re.IGNORECASE)
        if match:
            channels[match.group(1)] = channel

    versions = {match for match in suite_matches if match}
    development = next((version for version, channel in channels.items() if channel == "开发分支"), None)
    if "" in suite_matches and development:
        versions.add(development)

    releases: list[ToolchainRelease] = []
    for version in versions:
        channel = channels.get(version, "官方版本源")
        suite = f"llvm-toolchain-{codename}"
        if not (development == version and "" in suite_matches):
            suite = f"{suite}-{version}"
        releases.append(
            ToolchainRelease(
                version=version,
                label=f"LLVM/Clang {version}（apt.llvm.org {channel}）",
                provider=APT_LLVM_PROVIDER,
                url=f"{APT_LLVM_BASE_URL}/{codename}/",
                package_id=suite,
            )
        )
    return unique_sorted_releases(releases)


def system_package_toolchain_releases(toolchain: str, manager: str) -> tuple[ToolchainRelease, ...]:
    releases = list(apt_versioned_toolchain_releases(toolchain)) if manager == "apt-get" else []
    candidate = system_toolchain_candidate_version(toolchain, manager)
    candidate_label = f"（候选版本 {candidate}）" if candidate else ""
    releases.append(
        ToolchainRelease(
            version="system",
            label=f"{manager} 系统默认版本{candidate_label}",
            provider=SYSTEM_PACKAGE_PROVIDER,
            package_id="gcc" if toolchain == "gcc" else "clang",
        )
    )
    return unique_sorted_releases(releases)


def available_toolchain_releases(
    toolchain: str,
    *,
    llvm_variant: str = "auto",
    system: str | None = None,
) -> tuple[ToolchainRelease, ...]:
    host = normalized_system(system)
    if host != "windows":
        manager = detect_package_manager() or "系统包管理器"
        if manager == "系统包管理器":
            return (ToolchainRelease("system", "系统包管理器默认版本", SYSTEM_PACKAGE_PROVIDER),)
        releases: list[ToolchainRelease] = []
        if host == "linux" and manager == "apt-get" and toolchain == "llvm":
            releases.extend(apt_llvm_releases())
        releases.extend(system_package_toolchain_releases(toolchain, manager))
        return unique_sorted_releases(releases)
    if toolchain == "gcc":
        return winlibs_releases()
    if toolchain == "llvm":
        return llvm_msvc_releases() if llvm_variant == "msvc" else llvm_mingw_releases()
    if toolchain == "msvc":
        releases: list[ToolchainRelease] = []
        for label, package_id in MSVC_WINGET_PACKAGES:
            releases.extend(winget_package_versions(package_id, label))
        releases.extend(msvc_bootstrapper_releases())
        return unique_sorted_releases(releases)
    raise ValueError(f"未知工具链：{toolchain}")


def select_toolchain_release(
    releases: tuple[ToolchainRelease, ...],
    requested_version: str,
) -> ToolchainRelease | None:
    if not releases:
        return None
    if requested_version == "latest":
        return releases[0]
    exact = next((release for release in releases if release.version == requested_version), None)
    if exact:
        return exact
    prefix = f"{requested_version}."
    return next((release for release in releases if release.version.startswith(prefix)), None)


def find_first_child_with_bin(root: Path, executable: str) -> Path | None:
    direct = root / "bin" / executable
    if direct.exists():
        return root
    for child in root.iterdir():
        if child.is_dir() and (child / "bin" / executable).exists():
            return child
    return None


def install_zip_to_dir(archive: Path, install_dir: Path, executable: str) -> bool:
    if install_dir.exists():
        warn(f"目标目录已存在，不会覆盖：{install_dir}")
        return (install_dir / "bin" / executable).exists()

    with tempfile.TemporaryDirectory() as temp_dir:
        extract_root = Path(temp_dir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as zip_file:
                zip_file.extractall(extract_root)
        except (OSError, zipfile.BadZipFile) as exc:
            warn(f"解压失败：{exc}")
            return False

        source_root = find_first_child_with_bin(extract_root, executable)
        if not source_root:
            warn(f"解压后未找到 bin/{executable}。")
            return False

        install_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_root, install_dir)
    ok(f"已安装到：{install_dir}")
    return (install_dir / "bin" / executable).exists()


def download_and_install_zip(
    url: str,
    install_dir: Path,
    executable: str,
    *,
    digest: str | None = None,
) -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        archive = Path(temp_dir) / Path(urllib.parse.urlparse(url).path).name
        if not download_file(url, archive):
            return False
        if not verify_file_digest(archive, digest):
            return False
        return install_zip_to_dir(archive, install_dir, executable)


def latest_winlibs_url() -> str | None:
    releases = winlibs_releases(limit=1)
    if releases and releases[0].url:
        return releases[0].url
    html = fetch_text("https://winlibs.com/")
    if not html:
        return None
    urls = []
    for href in re.findall(r'href=["\']([^"\']+\.zip)["\']', html, flags=re.IGNORECASE):
        lower = href.lower()
        if all(token in lower for token in ("winlibs", "x86_64", "posix", "seh", "ucrt", "gcc")):
            urls.append(urllib.parse.urljoin("https://winlibs.com/", href))
    return urls[0] if urls else None


def latest_llvm_mingw_url() -> str | None:
    text = fetch_text("https://api.github.com/repos/mstorsjo/llvm-mingw/releases/latest")
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        warn("解析 llvm-mingw GitHub release JSON 失败。")
        return None
    for asset in data.get("assets", []):
        url = asset.get("browser_download_url", "")
        lower = url.lower()
        if lower.endswith(".zip") and "ucrt-x86_64" in lower:
            return url
    return None


def latest_llvm_msvc_url() -> str | None:
    text = fetch_text("https://api.github.com/repos/llvm/llvm-project/releases/latest")
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        warn("解析 LLVM GitHub release JSON 失败。")
        return None
    for asset in data.get("assets", []):
        url = asset.get("browser_download_url", "")
        name = asset.get("name", "").lower()
        if name.startswith("llvm-") and name.endswith("-win64.exe"):
            return url
    return None


def path_entries() -> list[str]:
    return [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def path_contains(path_to_find: Path) -> bool:
    return any(same_path(path_to_find, Path(entry)) for entry in path_entries())


def add_to_process_path(path_to_add: Path) -> None:
    if not path_contains(path_to_add):
        os.environ["PATH"] = f"{path_to_add}{os.pathsep}{os.environ.get('PATH', '')}"


def persist_path(path_to_add: Path) -> bool:
    path_str = str(path_to_add)
    if path_contains(path_to_add):
        ok(f"当前 PATH 已包含：{path_str}")
        return True

    log(f"准备把目录加入用户 PATH：{path_str}")
    if is_windows():
        ps_cmd = (
            "$target = " + repr(path_str) + "; "
            "$old = [Environment]::GetEnvironmentVariable('Path', 'User'); "
            "$items = @(); "
            "if ($old) { $items = $old -split ';' | Where-Object { $_ } }; "
            "if ($items -notcontains $target) { "
            "[Environment]::SetEnvironmentVariable('Path', (($items + $target) -join ';'), 'User') "
            "}"
        )
        result = run_command(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd])
        if result.returncode != 0:
            warn(f"无法写入用户 PATH，请手动加入：{path_str}")
            return False
    else:
        shell_name = os.environ.get("SHELL", "")
        rc_file = HOME / (".zshrc" if "zsh" in shell_name else ".bashrc")
        line = f'\n# Added by Templates setup\nexport PATH="{path_str}:$PATH"\n'
        try:
            content = rc_file.read_text(encoding="utf-8") if rc_file.exists() else ""
            if path_str not in content:
                with rc_file.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except OSError as exc:
            warn(f"无法写入 {rc_file}，请手动加入 PATH：{exc}")
            return False

    add_to_process_path(path_to_add)
    ok(f"已处理 PATH：{path_str}")
    return True


def find_executable(command: str) -> str | None:
    executable_name = f"{command}.exe" if is_windows() else command
    candidates = [PROJECT_VENV_BIN / executable_name, LOCAL_BIN / executable_name]
    if is_windows():
        candidates.append(UV_INSTALL_DIR / f"{command}.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return shutil.which(command)


def compiler_search_roots() -> list[Path]:
    if is_windows():
        return [resolve_case_insensitive_path(Path("C:/dev")), resolve_case_insensitive_path(Path("D:/dev"))]
    return [resolve_case_insensitive_path(HOME / "dev")]


def resolve_case_insensitive_path(path: Path) -> Path:
    if path.exists():
        return path
    parent = path.parent
    if not parent.exists():
        return path
    target = path.name.lower()
    try:
        for child in parent.iterdir():
            if child.name.lower() == target:
                return child
    except OSError:
        return path
    return path


def find_in_roots(executable_names: set[str]) -> list[Path]:
    found: list[Path] = []
    lowered = {name.lower() for name in executable_names}
    for root in compiler_search_roots():
        if not root.exists():
            continue
        try:
            for item in root.rglob("*"):
                if item.is_file() and item.name.lower() in lowered:
                    found.append(item)
        except OSError as exc:
            warn(f"扫描编译器目录失败：{root}，原因：{exc}")
    return found


def find_from_path(command: str) -> Path | None:
    found = shutil.which(command)
    return Path(found) if found else None


def homebrew_formula_bin_dirs(formula: str) -> list[Path]:
    if normalized_system() != "darwin":
        return []
    return [
        Path("/opt/homebrew/opt") / formula / "bin",
        Path("/usr/local/opt") / formula / "bin",
    ]


def compiler_version_text(executable: Path) -> str:
    try:
        result = capture_command([str(executable), "--version"])
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{result.stdout}\n{result.stderr}".lower()


def is_clang_driver(executable: Path) -> bool:
    text = compiler_version_text(executable)
    return "clang" in text or "llvm" in text


def is_msvc_abi_clang(executable: Path) -> bool:
    text = compiler_version_text(executable)
    return "clang" in text and "windows-msvc" in text


def managed_toolchain_bin_dirs(root: Path) -> list[Path]:
    bins: list[Path] = []
    if (root / "bin").is_dir():
        bins.append(root / "bin")
    if not root.is_dir():
        return bins
    try:
        version_dirs = [child for child in root.iterdir() if child.is_dir() and semantic_version_key(child.name)]
    except OSError:
        return bins
    version_dirs.sort(key=lambda child: semantic_version_key(child.name), reverse=True)
    bins[:0] = [child / "bin" for child in version_dirs if (child / "bin").is_dir()]
    return bins


def find_gcc_pair() -> tuple[str, str] | None:
    exe_suffix = ".exe" if is_windows() else ""
    candidates: list[tuple[Path, Path]] = []

    preferred_pair = PREFERRED_COMPILER_PAIRS.get("gcc")
    if preferred_pair:
        candidates.append(preferred_pair)
    preferred = PREFERRED_TOOLCHAIN_BINS.get("gcc")
    if preferred:
        candidates.append((preferred / f"gcc{exe_suffix}", preferred / f"g++{exe_suffix}"))
    for bin_dir in managed_toolchain_bin_dirs(GCC_DIR):
        candidates.append((bin_dir / f"gcc{exe_suffix}", bin_dir / f"g++{exe_suffix}"))

    path_gcc = find_from_path(f"gcc{exe_suffix}")
    path_gxx = find_from_path(f"g++{exe_suffix}")
    if path_gcc and path_gxx:
        candidates.append((path_gcc, path_gxx))

    if normalized_system() == "darwin":
        search_entries = [*path_entries(), *(str(path) for path in homebrew_formula_bin_dirs("gcc"))]
        for entry in search_entries:
            bin_dir = Path(entry)
            if not bin_dir.is_dir():
                continue
            try:
                for gxx in bin_dir.glob("g++-*"):
                    version = gxx.name.removeprefix("g++-")
                    gcc = bin_dir / f"gcc-{version}"
                    candidates.append((gcc, gxx))
            except OSError:
                continue

    for gxx in find_in_roots({f"g++{exe_suffix}"}):
        gcc = gxx.with_name(f"gcc{exe_suffix}")
        candidates.append((gcc, gxx))

    for gcc, gxx in candidates:
        if gcc.exists() and gxx.exists() and not is_clang_driver(gcc) and not is_clang_driver(gxx):
            return str(gcc), str(gxx)
    return None


def find_clang_pair() -> tuple[str, str] | None:
    exe_suffix = ".exe" if is_windows() else ""
    candidates: list[tuple[Path, Path]] = []

    preferred_pair = PREFERRED_COMPILER_PAIRS.get("clang")
    if preferred_pair:
        candidates.append(preferred_pair)
    preferred = PREFERRED_TOOLCHAIN_BINS.get("llvm-mingw")
    if preferred:
        candidates.append((preferred / f"clang{exe_suffix}", preferred / f"clang++{exe_suffix}"))
    for bin_dir in managed_toolchain_bin_dirs(CLANG_DIR):
        candidates.append((bin_dir / f"clang{exe_suffix}", bin_dir / f"clang++{exe_suffix}"))

    for bin_dir in homebrew_formula_bin_dirs("llvm"):
        candidates.append((bin_dir / "clang", bin_dir / "clang++"))

    path_clang = find_from_path(f"clang{exe_suffix}")
    path_clangxx = find_from_path(f"clang++{exe_suffix}")
    if path_clang and path_clangxx:
        candidates.append((path_clang, path_clangxx))

    for clangxx in find_in_roots({f"clang++{exe_suffix}"}):
        clang = clangxx.with_name(f"clang{exe_suffix}")
        candidates.append((clang, clangxx))

    for clang, clangxx in candidates:
        if is_windows() and is_msvc_abi_clang(clangxx):
            continue
        if clang.exists() and clangxx.exists():
            return str(clang), str(clangxx)
    return None


def find_clang_cl() -> str | None:
    exe_suffix = ".exe" if is_windows() else ""
    preferred = PREFERRED_TOOLCHAIN_BINS.get("llvm-msvc")
    if preferred:
        candidate = preferred / f"clang-cl{exe_suffix}"
        if candidate.exists() and (not is_windows() or is_msvc_abi_clang(candidate)):
            return str(candidate)
    for bin_dir in managed_toolchain_bin_dirs(CLANG_MSVC_DIR):
        candidate = bin_dir / f"clang-cl{exe_suffix}"
        if candidate.exists() and (not is_windows() or is_msvc_abi_clang(candidate)):
            return str(candidate)

    path_clang_cl = find_from_path(f"clang-cl{exe_suffix}")
    if path_clang_cl and (not is_windows() or is_msvc_abi_clang(path_clang_cl)):
        return str(path_clang_cl)

    candidates = find_in_roots({f"clang-cl{exe_suffix}"})
    for candidate in candidates:
        if not is_windows() or is_msvc_abi_clang(candidate):
            return str(candidate)
    return None


def visual_studio_installation_paths() -> list[Path]:
    if not is_windows():
        return []

    paths: list[Path] = []
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
    vswhere = program_files_x86 / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.exists():
        try:
            result = capture_command(
                [
                    str(vswhere),
                    "-products",
                    "*",
                    "-requires",
                    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                    "-property",
                    "installationPath",
                ]
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result and result.returncode == 0:
            paths.extend(Path(line.strip()) for line in result.stdout.splitlines() if line.strip())

    for root in (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Microsoft Visual Studio",
        program_files_x86 / "Microsoft Visual Studio",
    ):
        if not root.exists():
            continue
        try:
            for vc_tools in root.glob("*/*/VC/Tools/MSVC"):
                paths.append(vc_tools.parents[2])
        except OSError:
            continue

    unique: list[Path] = []
    for path in paths:
        if path.exists() and not any(same_path(path, existing) for existing in unique):
            unique.append(path)
    return unique


def msvc_target_arch(conan_arch: str) -> str:
    normalized = conan_arch.lower()
    if normalized in {"x86", "x86_32"}:
        return "x86"
    if normalized in {"armv8", "arm64", "aarch64"}:
        return "arm64"
    return "x64"


def find_msvc_cl(arch: str = "x86_64") -> str | None:
    exe_suffix = ".exe" if is_windows() else ""
    path_cl = find_from_path(f"cl{exe_suffix}")
    if path_cl:
        return str(path_cl)

    if not is_windows():
        return None

    host_arch = "Hostx64"
    target_arch = msvc_target_arch(arch)
    candidates: list[Path] = []
    for install_path in visual_studio_installation_paths():
        tools_root = install_path / "VC" / "Tools" / "MSVC"
        if not tools_root.exists():
            continue
        try:
            candidates.extend(tools_root.glob(f"*/bin/{host_arch}/{target_arch}/cl.exe"))
        except OSError:
            continue

    candidates = sorted(candidates, key=lambda path: path.parts, reverse=True)
    return str(candidates[0]) if candidates else None


def version_key(path: Path) -> tuple[int, ...]:
    parts: list[int] = []
    for token in path.name.split("."):
        if not token.isdigit():
            return ()
        parts.append(int(token))
    return tuple(parts)


def latest_existing_version_dir(root: Path, required_children: tuple[str, ...] = ()) -> Path | None:
    if not root.exists():
        return None

    candidates: list[Path] = []
    try:
        for child in root.iterdir():
            if not child.is_dir() or not version_key(child):
                continue
            if all((child / required_child).exists() for required_child in required_children):
                candidates.append(child)
    except OSError:
        return None

    return max(candidates, key=version_key) if candidates else None


def windows_sdk_root() -> Path:
    return Path(os.environ.get("WindowsSdkDir", r"C:\Program Files (x86)\Windows Kits\10"))


def msvc_build_environment(arch: str = "x86_64") -> dict[str, str] | None:
    if not is_windows():
        return None

    cl = find_msvc_cl(arch)
    if not cl:
        return None

    cl_path = Path(cl)
    target_arch = msvc_target_arch(arch)
    tools_root = cl_path.parents[3]
    install_path = tools_root.parents[3]
    sdk_root = windows_sdk_root()
    sdk_version_dir = latest_existing_version_dir(
        sdk_root / "include",
        ("ucrt", "um", "shared"),
    )
    if not sdk_version_dir:
        return None

    sdk_version = sdk_version_dir.name
    sdk_bin_version = sdk_root / "bin" / sdk_version
    sdk_bin_arch = sdk_bin_version / target_arch
    sdk_bin_fallback = sdk_root / "bin" / target_arch

    paths = [
        cl_path.parent,
        install_path / "Common7" / "IDE" / "VC" / "VCPackages",
        sdk_bin_arch,
        sdk_bin_fallback,
        install_path / "Common7" / "IDE",
        install_path / "Common7" / "Tools",
    ]
    include_paths = [
        tools_root / "include",
        install_path / "VC" / "Auxiliary" / "VS" / "include",
        sdk_root / "include" / sdk_version / "ucrt",
        sdk_root / "include" / sdk_version / "um",
        sdk_root / "include" / sdk_version / "shared",
        sdk_root / "include" / sdk_version / "winrt",
        sdk_root / "include" / sdk_version / "cppwinrt",
    ]
    lib_paths = [
        tools_root / "lib" / target_arch,
        sdk_root / "lib" / sdk_version / "ucrt" / target_arch,
        sdk_root / "lib" / sdk_version / "um" / target_arch,
    ]
    libpath_paths = [
        tools_root / "lib" / target_arch,
        Path(os.environ.get("FrameworkDir64", r"C:\Windows\Microsoft.NET\Framework64"))
        / os.environ.get("FrameworkVersion64", "v4.0.30319"),
    ]

    if not all(path.exists() for path in include_paths[:5]):
        return None
    if not all(path.exists() for path in lib_paths):
        return None
    if not (cl_path.parent / "link.exe").exists():
        return None
    if not any((path / "rc.exe").exists() for path in (sdk_bin_arch, sdk_bin_fallback)):
        return None
    if not any((path / "mt.exe").exists() for path in (sdk_bin_arch, sdk_bin_fallback)):
        return None

    return {
        "PATH": ";".join(str(path) for path in paths if path.exists()) + ";$penv{PATH}",
        "INCLUDE": ";".join(str(path) for path in include_paths if path.exists()),
        "LIB": ";".join(str(path) for path in lib_paths if path.exists()),
        "LIBPATH": ";".join(str(path) for path in libpath_paths if path.exists()),
    }


def has_msvc_build_environment(arch: str = "x86_64") -> bool:
    return msvc_build_environment(arch) is not None


def discovered_compilers() -> dict[str, str | tuple[str, str]]:
    compilers: dict[str, str | tuple[str, str]] = {}
    gcc = find_gcc_pair()
    if gcc:
        compilers["gcc"] = gcc
    clang = find_clang_pair()
    if clang:
        compilers["clang"] = clang
    msvc_env = has_msvc_build_environment()
    clang_cl = find_clang_cl()
    if clang_cl and msvc_env:
        compilers["clang_msvc"] = clang_cl
    msvc_cl = find_msvc_cl()
    if msvc_cl and msvc_env:
        compilers["msvc"] = msvc_cl
    return compilers


def compiler_full_version(executable: str, compiler: str) -> str | None:
    commands = [[executable, "--version"]]
    if compiler == "gcc":
        commands.insert(0, [executable, "-dumpfullversion", "-dumpversion"])
    for command in commands:
        try:
            result = capture_command(command)
        except (OSError, subprocess.SubprocessError):
            continue
        text = f"{result.stdout}\n{result.stderr}".strip()
        match = re.search(r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)", text)
        if result.returncode == 0 and match:
            return match.group(1)
    return None


def msvc_installation_version() -> str | None:
    if not is_windows():
        return None
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
    vswhere = program_files_x86 / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.exists():
        try:
            result = capture_command(
                [
                    str(vswhere),
                    "-latest",
                    "-products",
                    "*",
                    "-requires",
                    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                    "-property",
                    "installationVersion",
                ]
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result and result.returncode == 0:
            match = re.search(r"\d+(?:\.\d+)+", result.stdout)
            if match:
                return match.group(0)
    cl = find_msvc_cl()
    return compiler_full_version(cl, "msvc") if cl else None


def installed_toolchain_version(toolchain: str, llvm_variant: str = "auto") -> str | None:
    if toolchain == "gcc":
        pair = find_gcc_pair()
        return compiler_full_version(pair[1], "gcc") if pair else None
    if toolchain == "llvm":
        if llvm_variant == "msvc":
            clang_cl = find_clang_cl()
            return compiler_full_version(clang_cl, "clang") if clang_cl else None
        pair = find_clang_pair()
        return compiler_full_version(pair[1], "clang") if pair else None
    if toolchain == "msvc":
        return msvc_installation_version() if has_msvc_build_environment() else None
    raise ValueError(f"未知工具链：{toolchain}")


def prompt_toolchain_install_requests(
    selection: ToolchainSelection,
    *,
    system: str | None = None,
    input_fn=input,
) -> tuple[ToolchainInstallRequest, ...]:
    host = normalized_system(system)
    required = set(required_install_toolchains(selection, system))
    requests: list[ToolchainInstallRequest] = []
    print("\n[选择] 编译器安装与升级")
    print("  可逐项跳过；已有编译器不会在未确认时升级。")

    for toolchain in supported_install_toolchains(system):
        llvm_variant = "auto"
        choose_llvm_variant = toolchain == "llvm" and host == "windows" and selection.compiler != "clang"
        if toolchain == "llvm" and host == "windows" and not choose_llvm_variant:
            llvm_variant = llvm_variant_for_selection(selection, system)

        if choose_llvm_variant:
            variant_versions = {variant: installed_toolchain_version("llvm", variant) for variant in ("mingw", "msvc")}
            installed = next((version for version in variant_versions.values() if version), None)
        else:
            installed = installed_toolchain_version(toolchain, llvm_variant)
        label = TOOLCHAIN_LABELS[toolchain]
        if installed:
            if choose_llvm_variant:
                summary = "，".join(
                    f"{LLVM_VARIANT_LABELS[variant]} {version}"
                    for variant, version in variant_versions.items()
                    if version
                )
                log(f"{label} 已安装：{summary}")
                question = f"{label} 已安装 {summary}，是否选择模式和版本进行升级/切换"
            else:
                log(f"{label} 已安装版本：{installed}")
                question = f"{label} 当前版本 {installed}，是否选择版本并升级/切换"
            default = False
        else:
            log(f"{label} 当前未安装。")
            question = f"未检测到 {label}，是否安装"
            default = toolchain in required
        if not prompt_yes_no(question, default=default, input_fn=input_fn):
            continue

        if choose_llvm_variant:
            llvm_variant = prompt_choice(
                "LLVM Windows 运行模式",
                ("mingw", "msvc"),
                LLVM_VARIANT_LABELS,
                "mingw",
                input_fn,
            )
            installed = variant_versions[llvm_variant]

        releases = available_toolchain_releases(
            toolchain,
            llvm_variant=llvm_variant,
            system=system,
        )
        if releases:
            versions = tuple(dict.fromkeys(release.version for release in releases))
            release_by_version = {release.version: release for release in releases}
            labels = {version: release_by_version[version].label for version in versions}
            version = (
                prompt_choice(f"{label} 版本", versions, labels, versions[0], input_fn)
                if len(versions) > 1
                else versions[0]
            )
        else:
            warn(f"未能列出 {label} 的可用版本，将在安装时再次查询最新版本。")
            version = "latest"

        requests.append(
            ToolchainInstallRequest(
                toolchain=toolchain,
                version=version,
                upgrade=installed is not None,
                llvm_variant=llvm_variant,
                stdlib=selection.stdlib if toolchain == active_install_toolchain(selection) else "auto",
                linker=selection.linker if toolchain == active_install_toolchain(selection) else "auto",
            )
        )
    return tuple(requests)


def parse_toolchain_versions(values: list[str] | tuple[str, ...] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values or ():
        toolchain, separator, version = value.partition("=")
        toolchain = toolchain.strip().lower()
        version = version.strip()
        if separator != "=" or toolchain not in TOOLCHAIN_LABELS or not version:
            raise ValueError("--toolchain-version 必须使用 NAME=VERSION，例如 gcc=16.2.0")
        parsed[toolchain] = version
    return parsed


def noninteractive_toolchain_install_requests(
    selection: ToolchainSelection,
    toolchains: tuple[str, ...] | list[str],
    versions: dict[str, str] | None = None,
    *,
    upgrade: bool = False,
    llvm_variant: str = "auto",
    system: str | None = None,
) -> tuple[ToolchainInstallRequest, ...]:
    requested = list(dict.fromkeys(toolchains))
    if upgrade and not requested:
        requested = list(required_install_toolchains(selection, system))
    supported = supported_install_toolchains(system)
    invalid = [toolchain for toolchain in requested if toolchain not in supported]
    if invalid:
        raise ValueError(f"当前系统不支持安装：{', '.join(invalid)}；可选值：{', '.join(supported)}")

    version_map = versions or {}
    unused = [toolchain for toolchain in version_map if toolchain not in requested]
    if unused:
        raise ValueError(f"以下工具链指定了版本但未通过 --install-toolchain 选择：{', '.join(unused)}")

    requests: list[ToolchainInstallRequest] = []
    for toolchain in requested:
        variant = llvm_variant
        if toolchain == "llvm" and variant == "auto":
            variant = llvm_variant_for_selection(selection, system)
        requests.append(
            ToolchainInstallRequest(
                toolchain=toolchain,
                version=version_map.get(toolchain, "latest"),
                upgrade=upgrade,
                llvm_variant=variant,
                stdlib=selection.stdlib if toolchain == active_install_toolchain(selection) else "auto",
                linker=selection.linker if toolchain == active_install_toolchain(selection) else "auto",
            )
        )
    return tuple(requests)


def preset_group_for_selection(selection: ToolchainSelection) -> str:
    mode = selection_compiler_mode(selection)
    if mode == "clang_msvc":
        return "clang-msvc"
    if mode == "clang":
        return "clang-libcxx" if selection.stdlib == "libc++" else "clang-std"
    return mode


def available_cmake_preset_groups(
    default_settings: dict[str, str] | None = None,
    selection: ToolchainSelection | None = None,
) -> set[str]:
    system = platform.system()
    arch = conan_host_arch()
    groups: set[str] = set()

    if selection:
        if (
            selected_compiler_executables(selection)
            and supports_standard_library(selection)
            and linker_path(selection.linker, selection)
        ):
            groups.add(preset_group_for_selection(selection))
        return groups

    if find_gcc_pair():
        groups.add("gcc")

    if find_clang_pair():
        groups.add("clang-libcxx" if system == "Darwin" else "clang-std")

    msvc_env = has_msvc_build_environment(arch)
    if msvc_env:
        groups.add("msvc")
        if find_clang_cl():
            groups.add("clang-msvc")

    return groups


def cmake_preset_environments(
    default_settings: dict[str, str] | None = None,
    selection: ToolchainSelection | None = None,
) -> dict[str, dict[str, str]]:
    arch = conan_host_arch()
    msvc_env = msvc_build_environment(arch)
    if selection:
        group = preset_group_for_selection(selection)
        environment = dict(msvc_env or {}) if group in {"msvc", "clang-msvc"} else {}
        selected_linker = linker_path(selection.linker, selection)
        if selected_linker and not path_contains(selected_linker.parent):
            separator = ";" if is_windows() else ":"
            inherited_path = environment.get("PATH", "$penv{PATH}")
            environment["PATH"] = f"{selected_linker.parent}{separator}{inherited_path}"
        return {group: environment} if environment else {}
    if not msvc_env:
        return {}
    environments = {"msvc": msvc_env}
    if find_clang_cl():
        environments["clang-msvc"] = msvc_env
    return environments


def command_version(command: str) -> str:
    exe = find_executable(command)
    if not exe:
        return "未安装"

    for args in ([exe, "--version"], [exe, "version"]):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        output = (result.stdout or result.stderr).strip().splitlines()
        if result.returncode == 0 and output:
            return output[0]
    return exe


def compiler_major_version(executable: str, fallback: str) -> str:
    try:
        if "gcc" in Path(executable).name.lower() or "g++" in Path(executable).name.lower():
            result = capture_command([executable, "-dumpfullversion"])
            value = (result.stdout or "").strip()
            if value:
                return value.split(".")[0]
        result = capture_command([executable, "--version"])
    except (OSError, subprocess.SubprocessError):
        return fallback

    text = f"{result.stdout}\n{result.stderr}"
    for token in text.replace("(", " ").replace(")", " ").split():
        if token and token[0].isdigit() and "." in token:
            return token.split(".")[0]
    return fallback


def msvc_compiler_version(executable: str, fallback: str = DEFAULT_MSVC_VERSION) -> str:
    try:
        result = capture_command([executable])
    except (OSError, subprocess.SubprocessError):
        return fallback

    text = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"Version\s+19\.(\d+)", text)
    if not match:
        return fallback
    return f"19{int(match.group(1)) // 10}"


def msvc_profile_settings(default_settings: dict[str, str], msvc_cl: str, arch: str) -> dict[str, str]:
    settings = dict(default_settings)
    settings["os"] = "Windows"
    settings["arch"] = arch
    settings["compiler"] = "msvc"
    settings["compiler.version"] = msvc_compiler_version(msvc_cl)
    settings["compiler.cppstd"] = CONAN_CPPSTD
    settings["compiler.runtime"] = settings.get("compiler.runtime", "dynamic")
    settings["compiler.runtime_type"] = settings.get("compiler.runtime_type", settings.get("build_type", "Release"))
    settings["build_type"] = settings.get("build_type", "Release")
    settings.pop("compiler.libcxx", None)
    return settings


def install_uv(check_only: bool) -> str | None:
    uv = find_executable("uv")
    if uv:
        ok(f"uv 已安装：{uv}")
        return uv

    warn("未检测到 uv。")
    if check_only:
        warn("当前为仅检查模式，不会安装 uv。")
        return None

    log("开始安装 uv。")
    if is_windows():
        command = 'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"'
    else:
        command = "curl -LsSf https://astral.sh/uv/install.sh | sh"

    result = run_command(command, shell=True)
    if result.returncode != 0:
        error("uv 自动安装失败。请手动安装 uv: https://docs.astral.sh/uv/getting-started/installation/")
        return None

    add_to_process_path(LOCAL_BIN)
    if is_windows():
        add_to_process_path(UV_INSTALL_DIR)

    uv = find_executable("uv")
    if uv:
        ok(f"uv 安装完成：{uv}")
        return uv

    warn("uv 安装脚本执行完成，但当前终端仍无法定位 uv。请重新打开终端后再运行 setup.py。")
    return None


def install_with_uv(tool: Tool, uv: str, check_only: bool) -> bool:
    if not tool.uv_package:
        return False
    if check_only:
        warn(f"{tool.command} 缺失；仅检查模式下不安装。")
        return False

    result = run_command([uv, "tool", "install", tool.uv_package])
    if result.returncode == 0:
        ok(f"{tool.command} 已通过 uv 安装。")
        return True

    warn(f"{tool.command} 通过 uv 安装失败。")
    return False


def install_with_winget(tool: Tool, check_only: bool) -> bool:
    if not is_windows() or not tool.windows_winget_id:
        return False

    winget = shutil.which("winget")
    if not winget:
        warn("未检测到 winget，无法自动安装 Windows 系统工具。")
        return False

    if check_only:
        warn(f"{tool.command} 缺失；仅检查模式下不安装。")
        return False

    result = run_command(
        [
            winget,
            "install",
            "--id",
            tool.windows_winget_id,
            "-e",
            "--source",
            "winget",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ]
    )
    if result.returncode == 0:
        ok(f"{tool.command} 已通过 winget 安装。")
        return True

    warn(f"{tool.command} 通过 winget 安装失败。")
    return False


def detect_package_manager() -> str | None:
    host = normalized_system()
    if host == "windows":
        return "winget" if shutil.which("winget") else None
    if host == "darwin":
        return "brew" if shutil.which("brew") else None
    for manager in ("apt-get", "dnf", "pacman", "zypper"):
        if shutil.which(manager):
            return manager
    return None


def elevated_command(command: list[str]) -> list[str]:
    if normalized_system() in {"windows", "darwin"}:
        return command
    getuid = getattr(os, "geteuid", None)
    if getuid and getuid() == 0:
        return command
    return ["sudo", *command]


def install_system_packages(packages: tuple[str, ...], *, upgrade: bool = False) -> bool:
    manager = detect_package_manager()
    if not manager:
        warn("未识别可用的系统包管理器。")
        return False

    unique_packages = tuple(dict.fromkeys(packages))
    if manager == "brew":
        brew = shutil.which("brew") or "brew"
        if upgrade:
            result = run_command([brew, "upgrade", *unique_packages])
            if result.returncode == 0:
                return True
        return run_command([brew, "install", *unique_packages]).returncode == 0
    if manager == "apt-get":
        update = run_command(elevated_command([manager, "update"]))
        if update.returncode != 0:
            return False
        command = elevated_command([manager, "install", "-y", *unique_packages])
    elif manager == "dnf":
        command = elevated_command([manager, "install", "-y", *unique_packages])
    elif manager == "pacman":
        command = elevated_command([manager, "-S", "--needed", "--noconfirm", *unique_packages])
    else:
        command = elevated_command([manager, "--non-interactive", "install", *unique_packages])
    return run_command(command).returncode == 0


def verify_gpg_key_fingerprint(path: Path, expected: str) -> bool:
    gpg = shutil.which("gpg")
    if not gpg:
        warn("未找到 gpg，无法验证 apt.llvm.org 软件源密钥。")
        return False
    try:
        result = capture_command([gpg, "--show-keys", "--with-colons", str(path)])
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"读取 apt.llvm.org 软件源密钥失败：{exc}")
        return False
    fingerprints = re.findall(r"^fpr:::::::::([0-9A-F]+):", result.stdout, re.MULTILINE | re.IGNORECASE)
    if result.returncode == 0 and expected.upper() in {fingerprint.upper() for fingerprint in fingerprints}:
        ok("apt.llvm.org 软件源密钥指纹校验通过。")
        return True
    warn("apt.llvm.org 软件源密钥指纹不匹配，拒绝配置信任。")
    return False


def ensure_apt_llvm_repository(release: ToolchainRelease) -> bool:
    if (
        release.provider != APT_LLVM_PROVIDER
        or not re.fullmatch(r"\d+", release.version)
        or not release.url
        or not release.url.startswith(f"{APT_LLVM_BASE_URL}/")
        or not release.package_id
        or not re.fullmatch(r"llvm-toolchain-[a-z0-9]+(?:-\d+)?", release.package_id)
    ):
        warn("apt.llvm.org 版本元数据无效，拒绝修改软件源。")
        return False

    if not shutil.which("gpg") and not install_system_packages(("ca-certificates", "gnupg")):
        return False

    key_path = Path("/usr/share/keyrings/apt.llvm.org.asc")
    source_path = Path(f"/etc/apt/sources.list.d/apt-llvm-{release.version}.list")
    source_line = f"deb [signed-by={key_path}] {release.url} {release.package_id} main\n"

    key_ready = key_path.exists() and verify_gpg_key_fingerprint(key_path, APT_LLVM_KEY_FINGERPRINT)
    source_ready = False
    if source_path.exists():
        try:
            source_ready = source_path.read_text(encoding="utf-8") == source_line
        except OSError:
            source_ready = False
    if key_ready and source_ready:
        ok(f"apt.llvm.org LLVM {release.version} 软件源已配置。")
        return True

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        key_download = temp_root / "llvm-snapshot.gpg.key"
        source_file = temp_root / source_path.name
        if not key_ready:
            if not download_file(APT_LLVM_KEY_URL, key_download):
                return False
            if not verify_gpg_key_fingerprint(key_download, APT_LLVM_KEY_FINGERPRINT):
                return False
            install_program = shutil.which("install") or "install"
            if run_command(
                elevated_command([install_program, "-m", "0644", str(key_download), str(key_path)])
            ).returncode:
                return False
        if not source_ready:
            source_file.write_text(source_line, encoding="utf-8")
            install_program = shutil.which("install") or "install"
            if run_command(
                elevated_command([install_program, "-m", "0644", str(source_file), str(source_path)])
            ).returncode:
                return False

    ok(f"已配置 apt.llvm.org LLVM {release.version} 软件源：{release.package_id}")
    return True


def versioned_executable(name: str) -> Path | None:
    found = shutil.which(name)
    if found:
        return Path(found)
    candidate = Path("/usr/bin") / name
    return candidate if candidate.exists() else None


def prefer_versioned_compiler(toolchain: str, c_name: str, cpp_name: str) -> bool:
    c_compiler = versioned_executable(c_name)
    cpp_compiler = versioned_executable(cpp_name)
    if not c_compiler or not cpp_compiler:
        return False
    PREFERRED_COMPILER_PAIRS[toolchain] = (c_compiler, cpp_compiler)
    ok(f"已选择编译器：{c_compiler} / {cpp_compiler}")
    return True


def install_versioned_apt_llvm(version: str, *, stdlib: str = "auto", linker: str = "auto") -> bool:
    packages = [f"clang-{version}"]
    if stdlib == "libc++":
        packages.extend((f"libc++-{version}-dev", f"libc++abi-{version}-dev"))
    if linker == "lld":
        packages.append(f"lld-{version}")
    if not install_system_packages(tuple(packages), upgrade=True):
        return False
    if not prefer_versioned_compiler("clang", f"clang-{version}", f"clang++-{version}"):
        warn(f"LLVM/Clang {version} 安装后未找到版本化编译器。")
        return False
    if linker == "lld":
        preferred_lld = versioned_executable(f"ld.lld-{version}") or versioned_executable(f"lld-{version}")
        if not preferred_lld:
            warn(f"LLVM lld {version} 安装后仍不可用。")
            return False
        PREFERRED_LINKERS["lld"] = preferred_lld
        ok(f"已选择链接器：{preferred_lld}")
    return True


def install_apt_llvm_release(release: ToolchainRelease, *, stdlib: str = "auto", linker: str = "auto") -> bool:
    if not ensure_apt_llvm_repository(release):
        return False
    return install_versioned_apt_llvm(release.version, stdlib=stdlib, linker=linker)


def packages_for(kind: str, manager: str) -> tuple[str, ...]:
    package_map = {
        "gcc": {
            "apt-get": ("gcc", "g++"),
            "dnf": ("gcc", "gcc-c++"),
            "pacman": ("gcc",),
            "zypper": ("gcc", "gcc-c++"),
            "brew": ("gcc",),
        },
        "clang": {
            "apt-get": ("clang",),
            "dnf": ("clang",),
            "pacman": ("clang",),
            "zypper": ("clang",),
            "brew": ("llvm",),
        },
        "lld": {
            "apt-get": ("lld",),
            "dnf": ("lld",),
            "pacman": ("lld",),
            "zypper": ("lld",),
            "brew": ("lld",),
        },
        "bfd": {
            "apt-get": ("binutils",),
            "dnf": ("binutils",),
            "pacman": ("binutils",),
            "zypper": ("binutils",),
            "brew": ("binutils",),
        },
        "mold": {
            "apt-get": ("mold",),
            "dnf": ("mold",),
            "pacman": ("mold",),
            "zypper": ("mold",),
            "brew": ("mold",),
        },
        "libc++": {
            "apt-get": ("libc++-dev", "libc++abi-dev"),
            "dnf": ("libcxx-devel", "libcxxabi-devel"),
            "pacman": ("libc++",),
            "zypper": ("libc++-devel", "libc++abi-devel"),
            "brew": ("llvm",),
        },
        "libstdc++": {
            "apt-get": ("g++",),
            "dnf": ("libstdc++-devel",),
            "pacman": ("gcc",),
            "zypper": ("gcc-c++",),
            "brew": ("gcc",),
        },
    }
    return package_map.get(kind, {}).get(manager, ())


def planned_system_packages(
    selection: ToolchainSelection,
    manager: str,
    *,
    compiler_missing: bool,
    stdlib_missing: bool,
    linker_missing: bool,
) -> tuple[str, ...]:
    packages: list[str] = []
    if compiler_missing:
        packages.extend(packages_for(selection.compiler, manager))
    if stdlib_missing and selection.stdlib != "msvc":
        packages.extend(packages_for(selection.stdlib, manager))
    if linker_missing and selection.linker not in {"system", "msvc"}:
        packages.extend(packages_for(selection.linker, manager))
    return tuple(dict.fromkeys(packages))


def requested_release(toolchain: str, version: str, llvm_variant: str = "auto") -> ToolchainRelease | None:
    releases = available_toolchain_releases(toolchain, llvm_variant=llvm_variant)
    release = select_toolchain_release(releases, version)
    if not release:
        warn(f"未找到 {TOOLCHAIN_LABELS[toolchain]} 版本 {version}。")
    return release


def install_gcc(check_only: bool, *, version: str | None = None, upgrade: bool = False) -> bool:
    installed = installed_toolchain_version("gcc")
    if installed and not upgrade:
        ok(f"已检测到 GCC/G++ {installed}。")
        return True

    if check_only:
        warn("未检测到 GCC/G++。" if not installed else f"GCC/G++ 当前版本：{installed}；未执行升级检查。")
        return installed is not None

    if is_windows():
        requested_version = version or "latest"
        release = requested_release("gcc", requested_version)
        if not release or not release.url:
            warn(f"无法解析所选 WinLibs GCC 版本；请检查网络或手动安装到：{GCC_DIR}")
            return False
        if installed == release.version:
            ok(f"GCC/G++ 已是所选版本 {release.version}。")
            return True
        if installed and requested_version == "latest" and version_is_at_least(installed, release.version):
            ok(f"GCC/G++ 当前版本 {installed} 不低于最新可用版本 {release.version}。")
            return True
        install_dir = GCC_DIR / release.version
        bin_dir = install_dir / "bin"
        if (bin_dir / "g++.exe").exists():
            PREFERRED_TOOLCHAIN_BINS["gcc"] = bin_dir
            add_to_process_path(bin_dir)
            ok(f"已切换到现有 GCC/G++ {release.version}：{install_dir}")
            return True
        if download_and_install_zip(
            release.url,
            install_dir,
            "g++.exe",
            digest=release.digest,
        ):
            PREFERRED_TOOLCHAIN_BINS["gcc"] = bin_dir
            add_to_process_path(bin_dir)
            ok(f"GCC/G++ {release.version} 已通过 WinLibs 安装到：{install_dir}")
            return True
        warn(f"GCC/G++ {release.version} 自动安装失败。")
        return False

    manager = detect_package_manager()
    requested_version = version or "latest"
    if manager == "apt-get":
        release = requested_release("gcc", requested_version)
        if not release:
            return False
        if release.version == "system":
            packages = packages_for("gcc", manager)
        elif release.provider == SYSTEM_PACKAGE_PROVIDER and re.fullmatch(r"\d+", release.version):
            packages = (f"gcc-{release.version}", f"g++-{release.version}")
        else:
            warn(f"无法解析 GCC {release.version} 的 APT 安装包。")
            return False
        if not install_system_packages(packages, upgrade=upgrade):
            return False
        if release.version != "system" and not prefer_versioned_compiler(
            "gcc", f"gcc-{release.version}", f"g++-{release.version}"
        ):
            warn(f"GCC/G++ {release.version} 安装后未找到版本化编译器。")
            return False
        return bool(find_gcc_pair())

    if requested_version not in {"latest", "system"}:
        warn(f"{manager or '当前系统'} 暂不支持安装指定 GCC 版本 {requested_version}。")
        return False
    packages = packages_for("gcc", manager) if manager else ()
    if not packages:
        warn("未识别可自动安装 GCC 的包管理器。")
        return False
    return install_system_packages(packages, upgrade=upgrade) and bool(find_gcc_pair())


def install_clang(
    check_only: bool,
    *,
    version: str | None = None,
    upgrade: bool = False,
    stdlib: str = "auto",
    linker: str = "auto",
) -> bool:
    installed = installed_toolchain_version("llvm", "mingw" if is_windows() else "auto")
    if installed and not upgrade:
        ok(f"已检测到 Clang/Clang++ {installed}。")
        return True

    if check_only:
        warn("未检测到 Clang/Clang++。" if not installed else f"Clang/Clang++ 当前版本：{installed}；未执行升级检查。")
        return installed is not None

    if is_windows():
        requested_version = version or "latest"
        release = requested_release("llvm", requested_version, "mingw")
        if not release or not release.url:
            warn(f"无法解析所选 llvm-mingw 版本；请检查网络或手动安装到：{CLANG_DIR}")
            return False
        if installed == release.version:
            ok(f"Clang/Clang++ 已是所选版本 {release.version}。")
            return True
        if installed and requested_version == "latest" and version_is_at_least(installed, release.version):
            ok(f"Clang/Clang++ 当前版本 {installed} 不低于最新可用版本 {release.version}。")
            return True
        install_dir = CLANG_DIR / release.version
        bin_dir = install_dir / "bin"
        if (bin_dir / "clang++.exe").exists():
            PREFERRED_TOOLCHAIN_BINS["llvm-mingw"] = bin_dir
            add_to_process_path(bin_dir)
            ok(f"已切换到现有 LLVM/MinGW {release.version}：{install_dir}")
            return True
        if download_and_install_zip(
            release.url,
            install_dir,
            "clang++.exe",
            digest=release.digest,
        ):
            PREFERRED_TOOLCHAIN_BINS["llvm-mingw"] = bin_dir
            add_to_process_path(bin_dir)
            ok(f"Clang/Clang++ {release.version} 已通过 llvm-mingw 安装到：{install_dir}")
            return True
        warn(f"Clang/Clang++ {release.version} 自动安装失败。")
        return False

    manager = detect_package_manager()
    requested_version = version or "latest"
    if manager == "apt-get":
        release = requested_release("llvm", requested_version)
        if not release:
            return False
        if release.provider == APT_LLVM_PROVIDER:
            return install_apt_llvm_release(release, stdlib=stdlib, linker=linker)
        if release.version != "system" and re.fullmatch(r"\d+", release.version):
            return install_versioned_apt_llvm(release.version, stdlib=stdlib, linker=linker)
        packages = packages_for("clang", manager)
        return install_system_packages(packages, upgrade=upgrade) and bool(find_clang_pair())

    if requested_version not in {"latest", "system"}:
        warn(f"{manager or '当前系统'} 暂不支持安装指定 LLVM 版本 {requested_version}。")
        return False
    if normalized_system() == "darwin" and manager != "brew":
        warn("未检测到 Homebrew；请先安装 Homebrew，或使用 xcode-select --install 安装系统 Clang。")
        return False
    packages = packages_for("clang", manager) if manager else ()
    if not packages:
        warn("未识别可自动安装 Clang 的包管理器。")
        return False
    return install_system_packages(packages, upgrade=upgrade) and bool(find_clang_pair())


def install_clang_msvc(check_only: bool, *, version: str | None = None, upgrade: bool = False) -> bool:
    if not is_windows():
        return True
    installed = installed_toolchain_version("llvm", "msvc")
    if installed and not upgrade:
        ok(f"已检测到 clang-cl {installed}。")
        return True
    if check_only:
        warn("未检测到 clang-cl。" if not installed else f"clang-cl 当前版本：{installed}；未执行升级检查。")
        return installed is not None

    requested_version = version or "latest"
    release = requested_release("llvm", requested_version, "msvc")
    if not release or not release.url:
        warn(f"无法解析所选 LLVM Windows 安装器；请检查网络或手动安装到：{CLANG_MSVC_DIR}")
        return False
    if installed == release.version:
        ok(f"clang-cl 已是所选版本 {release.version}。")
        return True
    if installed and requested_version == "latest" and version_is_at_least(installed, release.version):
        ok(f"clang-cl 当前版本 {installed} 不低于最新可用版本 {release.version}。")
        return True

    install_dir = CLANG_MSVC_DIR / release.version
    bin_dir = install_dir / "bin"
    if (bin_dir / "clang-cl.exe").exists():
        PREFERRED_TOOLCHAIN_BINS["llvm-msvc"] = bin_dir
        add_to_process_path(bin_dir)
        ok(f"已切换到现有 clang-cl {release.version}：{install_dir}")
        return True

    with tempfile.TemporaryDirectory() as temp_dir:
        installer = Path(temp_dir) / Path(urllib.parse.urlparse(release.url).path).name
        if not download_file(release.url, installer) or not verify_file_digest(installer, release.digest):
            return False
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        result = run_command([str(installer), "/S", f"/D={install_dir}"])
        if result.returncode == 0 and (bin_dir / "clang-cl.exe").exists():
            PREFERRED_TOOLCHAIN_BINS["llvm-msvc"] = bin_dir
            add_to_process_path(bin_dir)
            ok(f"clang-cl {release.version} 已安装到：{install_dir}")
            return True

    warn(f"clang-cl {release.version} 自动安装失败。")
    return False


def install_msvc(check_only: bool, *, version: str | None = None, upgrade: bool = False) -> bool:
    if not is_windows():
        warn("MSVC 仅支持 Windows。")
        return False
    installed = installed_toolchain_version("msvc")
    if installed and not upgrade:
        ok(f"已检测到完整 MSVC 构建环境 {installed}。")
        return True
    if check_only:
        warn("未检测到完整 MSVC 构建环境。" if not installed else f"MSVC 当前版本：{installed}；未执行升级检查。")
        return installed is not None

    requested_version = version or "latest"
    release = requested_release("msvc", requested_version)
    if not release:
        return False
    target_version = release.version if release else version or "latest"
    if installed and target_version == installed:
        ok(f"MSVC 已是所选版本 {installed}。")
        return True
    if installed and requested_version == "latest" and version_is_at_least(installed, target_version):
        ok(f"MSVC 当前版本 {installed} 不低于最新可用版本 {target_version}。")
        return True

    winget = shutil.which("winget")
    if winget and release.package_id:
        same_major = bool(installed and semantic_version_key(installed) and semantic_version_key(target_version)) and (
            semantic_version_key(installed)[0] == semantic_version_key(target_version)[0]
        )
        command = [
            winget,
            "upgrade" if installed and same_major else "install",
            "--id",
            release.package_id,
            "-e",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--version",
            release.version,
            "--override",
            "--passive --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended",
        ]
        result = run_command(command)
    elif release.url:
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "vs_buildtools.exe"
            if not download_file(release.url, installer):
                return False
            verified = (
                verify_file_digest(installer, release.digest)
                if release.digest
                else verify_microsoft_authenticode(installer)
            )
            if not verified:
                return False
            result = run_command(
                [
                    str(installer),
                    "--passive",
                    "--wait",
                    "--norestart",
                    "--add",
                    "Microsoft.VisualStudio.Workload.VCTools",
                    "--includeRecommended",
                ]
            )
    else:
        warn("未检测到 winget，且所选 MSVC 版本没有可用的 Microsoft bootstrapper。")
        return False
    if result.returncode in {0, 3010} and has_msvc_build_environment():
        ok(f"MSVC、link.exe 和 Windows SDK 已安装/升级到所选版本 {target_version}。")
        return True
    warn("Visual Studio Build Tools 操作后仍未发现完整 C++ workload；请在 Visual Studio Installer 中检查安装。")
    return False


def selected_compiler_executables(selection: ToolchainSelection) -> tuple[str, str] | None:
    mode = selection_compiler_mode(selection)
    if mode == "gcc":
        return find_gcc_pair()
    if mode == "clang":
        return find_clang_pair()
    if mode == "clang_msvc":
        clang_cl = find_clang_cl()
        return (clang_cl, clang_cl) if clang_cl and has_msvc_build_environment() else None
    msvc_cl = find_msvc_cl()
    return (msvc_cl, msvc_cl) if msvc_cl and has_msvc_build_environment() else None


def find_lld(selection: ToolchainSelection | None = None) -> Path | None:
    preferred = PREFERRED_LINKERS.get("lld")
    if preferred and preferred.exists():
        return preferred
    if is_windows():
        msvc_abi = selection and selection_compiler_mode(selection) in {"msvc", "clang_msvc"}
        names = ("lld-link.exe",) if msvc_abi else ("ld.lld.exe", "lld.exe")
    elif normalized_system() == "darwin":
        names = ("ld64.lld", "lld")
    else:
        names = ("ld.lld", "lld")

    compiler_paths = selected_compiler_executables(selection) if selection else None
    fallback_paths = (find_clang_cl(), *(find_clang_pair() or ()))
    for compiler in (*(compiler_paths or ()), *fallback_paths):
        if not compiler:
            continue
        parent = Path(compiler).parent
        for name in names:
            candidate = parent / name
            if candidate.exists():
                return candidate
    for name in names:
        found = find_from_path(name)
        if found:
            return found
    for bin_dir in homebrew_formula_bin_dirs("lld"):
        for name in names:
            candidate = bin_dir / name
            if candidate.exists():
                return candidate
    return None


def find_bfd_linker() -> Path | None:
    gcc_pair = find_gcc_pair()
    names = ("ld.exe", "ld.bfd.exe") if is_windows() else ("ld.bfd", "ld")
    if gcc_pair:
        parent = Path(gcc_pair[1]).parent
        for name in names:
            candidate = parent / name
            if candidate.exists():
                return candidate
    if normalized_system() == "darwin":
        return None
    for name in names:
        found = find_from_path(name)
        if found:
            return found
    return None


def find_msvc_link(arch: str = "x86_64") -> Path | None:
    cl = find_msvc_cl(arch)
    if not cl or not has_msvc_build_environment(arch):
        return None
    candidate = Path(cl).with_name("link.exe")
    return candidate if candidate.exists() else None


def find_system_linker() -> Path | None:
    if is_windows():
        return find_msvc_link()
    if normalized_system() == "darwin":
        try:
            result = capture_command(["xcrun", "--find", "ld"])
        except (OSError, subprocess.SubprocessError):
            return None
        path = Path(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None
        return path if path and path.exists() else None
    found = find_from_path("ld")
    return found


def linker_path(linker: str, selection: ToolchainSelection | None = None) -> Path | None:
    if linker == "lld":
        return find_lld(selection)
    if linker == "bfd":
        return find_bfd_linker()
    if linker == "mold":
        return find_from_path("mold")
    if linker == "msvc":
        return find_msvc_link()
    return find_system_linker()


def supports_standard_library(selection: ToolchainSelection) -> bool:
    if selection.stdlib == "msvc":
        return has_msvc_build_environment()
    executables = selected_compiler_executables(selection)
    if not executables:
        return False
    if selection.compiler == "gcc":
        return selection.stdlib == "libstdc++"

    command = [executables[1], f"-stdlib={selection.stdlib}", "-x", "c++", "-fsyntax-only", "-"]
    try:
        result = subprocess.run(
            command,
            input="#include <vector>\nint main() { std::vector<int> value; return value.empty(); }\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def install_standard_library(selection: ToolchainSelection, check_only: bool) -> bool:
    if supports_standard_library(selection):
        ok(f"已检测到 {STDLIB_LABELS[selection.stdlib]}。")
        return True
    warn(f"当前工具链无法使用 {STDLIB_LABELS[selection.stdlib]}。")
    if check_only:
        return False
    if selection.stdlib == "msvc":
        return install_msvc(False)
    if is_windows() or normalized_system() == "darwin":
        warn("所选标准库应由对应编译器工具链提供，但安装后仍未通过编译检查。")
        return False
    manager = detect_package_manager()
    packages = packages_for(selection.stdlib, manager) if manager else ()
    if not packages:
        warn(f"当前包管理器没有 {selection.stdlib} 的自动安装映射。")
        return False
    return install_system_packages(packages) and supports_standard_library(selection)


def install_linker(selection: ToolchainSelection, check_only: bool) -> bool:
    found = linker_path(selection.linker, selection)
    if found:
        ok(f"已检测到 {LINKER_LABELS[selection.linker]}：{found}")
        return True
    warn(f"未检测到 {LINKER_LABELS[selection.linker]}。")
    if check_only:
        return False

    if selection.linker == "msvc":
        installed = install_msvc(False)
    elif selection.linker == "system":
        warn("系统默认链接器应由操作系统开发工具提供，请安装平台开发工具后重试。")
        return False
    elif is_windows():
        if selection.linker == "bfd":
            installed = install_gcc(False)
        else:
            installed = install_clang_msvc(False)
    else:
        manager = detect_package_manager()
        packages = packages_for(selection.linker, manager) if manager else ()
        installed = bool(packages) and install_system_packages(packages)

    found = linker_path(selection.linker, selection)
    if installed and found:
        ok(f"{LINKER_LABELS[selection.linker]} 已安装：{found}")
        return True
    warn(f"{LINKER_LABELS[selection.linker]} 安装后仍不可用。")
    return False


def install_toolchain_request(request: ToolchainInstallRequest, check_only: bool) -> bool:
    label = TOOLCHAIN_LABELS[request.toolchain]
    action = "升级/切换" if request.upgrade else "安装"
    log(f"准备{action} {label}，目标版本：{request.version}")
    if request.toolchain == "gcc":
        return install_gcc(check_only, version=request.version, upgrade=request.upgrade)
    if request.toolchain == "llvm":
        if request.llvm_variant == "msvc":
            return install_clang_msvc(check_only, version=request.version, upgrade=request.upgrade)
        return install_clang(
            check_only,
            version=request.version,
            upgrade=request.upgrade,
            stdlib=request.stdlib,
            linker=request.linker,
        )
    if request.toolchain == "msvc":
        return install_msvc(check_only, version=request.version, upgrade=request.upgrade)
    raise ValueError(f"未知工具链：{request.toolchain}")


def install_toolchain_requests(requests: tuple[ToolchainInstallRequest, ...], check_only: bool) -> bool:
    all_ok = True
    for request in requests:
        if not install_toolchain_request(request, check_only):
            all_ok = False
    return all_ok


def install_selected_compiler(selection: ToolchainSelection, check_only: bool) -> bool:
    mode = selection_compiler_mode(selection)
    if mode == "gcc":
        return install_gcc(check_only)
    if mode == "clang":
        return install_clang(check_only)
    if mode == "msvc":
        return install_msvc(check_only)
    msvc_ok = install_msvc(check_only)
    clang_ok = install_clang_msvc(check_only)
    return msvc_ok and clang_ok


def prepare_selected_toolchain(
    selection: ToolchainSelection,
    check_only: bool,
    *,
    allow_compiler_install: bool = True,
) -> bool:
    if not check_only and not is_windows():
        manager = detect_package_manager()
        if manager:
            packages = planned_system_packages(
                selection,
                manager,
                compiler_missing=allow_compiler_install and selected_compiler_executables(selection) is None,
                stdlib_missing=not supports_standard_library(selection),
                linker_missing=linker_path(selection.linker, selection) is None,
            )
            if packages:
                log(f"按 {manager} 合并安装所选工具链软件包：{', '.join(packages)}")
                install_system_packages(packages)

    if allow_compiler_install:
        compiler_ok = install_selected_compiler(selection, check_only)
    else:
        compiler_ok = selected_compiler_executables(selection) is not None
        if not compiler_ok:
            warn("所选主编译器未安装，且交互安装计划已选择跳过。")
    stdlib_ok = compiler_ok and install_standard_library(selection, check_only)
    linker_ok = compiler_ok and install_linker(selection, check_only)
    if compiler_ok and stdlib_ok and linker_ok:
        ok("所选 C++ 工具链检查通过。")
        return True
    warn("所选 C++ 工具链尚未完全就绪。")
    return False


def check_compiler() -> bool:
    found = False
    gcc_pair = find_gcc_pair()
    if gcc_pair:
        ok(f"检测到 GCC：{gcc_pair[1]}")
        found = True

    clang_pair = find_clang_pair()
    if clang_pair:
        ok(f"检测到 llvm-mingw Clang：{clang_pair[1]}")
        found = True

    msvc_env = has_msvc_build_environment()
    clang_cl = find_clang_cl()
    if clang_cl and msvc_env:
        ok(f"检测到可用 clang-cl/MSVC 工具链：{clang_cl}")
        found = True
    elif clang_cl:
        warn(f"检测到 clang-cl，但当前终端没有完整 VS 构建环境：{clang_cl}")

    cl = find_msvc_cl()
    if cl and msvc_env:
        ok(f"检测到完整 MSVC 构建环境：{cl}")
        found = True
    elif cl:
        warn(f"检测到 MSVC cl，但当前终端没有完整 VS 构建环境：{cl}")
        warn("请在 Developer Command Prompt / VsDevCmd.bat 初始化后的终端中运行 MSVC/clang-cl preset。")

    if found:
        return True

    warn("未检测到 C++ 编译器。Conan/CMake 已可安装，但实际构建仍需要编译器。")
    if is_windows():
        warn("建议安装 Visual Studio Build Tools，勾选 C++ build tools；或安装 LLVM/MinGW 并配置 Conan profile。")
    else:
        warn("请使用系统包管理器安装 clang 或 gcc。")
    return False


def conan_profiles_dir() -> Path:
    return CONAN_HOME / "profiles"


def read_default_profile_settings() -> dict[str, str]:
    profile = conan_profiles_dir() / "default"
    settings: dict[str, str] = {}
    section = None
    if not profile.exists():
        return settings

    for raw_line in profile.read_text(encoding="utf-8").splitlines():
        line = raw_line.lstrip("\ufeff").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "settings" and "=" in line:
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip()
    return settings


def write_profile(
    name: str,
    settings: dict[str, str],
    compiler_executables: dict[str, str],
    linker: str | None = None,
    linker_executable: Path | None = None,
) -> None:
    conan_profiles_dir().mkdir(parents=True, exist_ok=True)
    profile = conan_profiles_dir() / name
    lines = ["[settings]"]
    for key in (
        "os",
        "arch",
        "compiler",
        "compiler.version",
        "compiler.cppstd",
        "compiler.runtime",
        "compiler.runtime_type",
        "compiler.libcxx",
        "build_type",
    ):
        if key in settings:
            lines.append(f"{key}={settings[key]}")
    lines.extend(["", "[conf]", f"tools.build:compiler_executables={json.dumps(compiler_executables)}"])
    if linker:
        linker_variable = {
            "CMAKE_LINKER_TYPE": {
                "value": CMAKE_LINKER_TYPES[linker],
                "cache": True,
                "type": "STRING",
            }
        }
        lines.append(f"tools.cmake.cmaketoolchain:extra_variables={linker_variable!r}")
    lines.append(f"tools.cmake.cmaketoolchain:generator={CONAN_CMAKE_GENERATOR}")
    if linker_executable and not path_contains(linker_executable.parent):
        lines.extend(["", "[buildenv]", f"PATH=+(path){linker_executable.parent}"])
    lines.append("")
    profile.write_text("\n".join(lines), encoding="utf-8")
    ok(f"已生成 Conan profile：{profile}")


def remove_stale_compiler_profiles(active_profile_names: set[str]) -> None:
    stale_names = MANAGED_PROFILE_NAMES - active_profile_names
    for name in sorted(stale_names):
        profile = conan_profiles_dir() / name
        if not profile.exists():
            continue
        try:
            profile.unlink()
            warn(f"已删除不可用工具链对应的 Conan profile：{profile}")
        except OSError as exc:
            warn(f"无法删除不可用工具链对应的 Conan profile {profile}：{exc}")


def promote_single_compiler_profile_to_default(active_profile_names: set[str]) -> None:
    if len(active_profile_names) != 1:
        return

    source_name = next(iter(active_profile_names))
    source = conan_profiles_dir() / source_name
    target = conan_profiles_dir() / "default"
    if not source.exists():
        return

    try:
        shutil.copy2(source, target)
        ok(f"已将唯一 Conan profile 设为 default：{source_name}")
    except OSError as exc:
        warn(f"无法将唯一 Conan profile 设为 default：{exc}")


def generate_compiler_profiles(selection: ToolchainSelection | None = None) -> bool:
    default_settings = read_default_profile_settings()
    system = platform.system()
    arch = conan_host_arch()
    active_profile_names: set[str] = set()

    selection = selection or resolve_toolchain_selection(interactive=False)
    mode = selection_compiler_mode(selection)
    generated = False

    if mode == "gcc":
        compiler_pair = find_gcc_pair()
        if compiler_pair:
            gcc, gxx = compiler_pair
            write_profile(
                "gcc",
                {
                    "os": system,
                    "arch": arch,
                    "compiler": "gcc",
                    "compiler.version": compiler_major_version(gxx, "13"),
                    "compiler.cppstd": CONAN_CPPSTD,
                    "compiler.libcxx": "libstdc++11",
                    "build_type": default_settings.get("build_type", "Release"),
                },
                {"c": gcc, "cpp": gxx},
                selection.linker,
                linker_path(selection.linker, selection),
            )
            active_profile_names.add("gcc")
            generated = True
        else:
            warn("未生成 gcc profile：没有找到 gcc/g++ 可执行文件。")
    elif mode == "clang":
        compiler_pair = find_clang_pair()
        if compiler_pair:
            clang, clangxx = compiler_pair
            write_profile(
                "clang",
                {
                    "os": system,
                    "arch": arch,
                    "compiler": "clang",
                    "compiler.version": compiler_major_version(clangxx, "17"),
                    "compiler.cppstd": CONAN_CPPSTD,
                    "compiler.libcxx": "libc++" if selection.stdlib == "libc++" else "libstdc++11",
                    "build_type": default_settings.get("build_type", "Release"),
                },
                {"c": clang, "cpp": clangxx},
                selection.linker,
                linker_path(selection.linker, selection),
            )
            active_profile_names.add("clang")
            generated = True
        else:
            warn("未生成 clang profile：没有找到 clang/clang++ 可执行文件。")
    else:
        msvc_cl = find_msvc_cl(arch)
        msvc_settings = (
            msvc_profile_settings(default_settings, msvc_cl, arch)
            if msvc_cl and has_msvc_build_environment(arch)
            else None
        )
        compiler = find_clang_cl() if mode == "clang_msvc" else msvc_cl
        profile_name = "clang_msvc" if mode == "clang_msvc" else "msvc"
        if compiler and msvc_settings:
            write_profile(
                profile_name,
                msvc_settings,
                {"c": compiler, "cpp": compiler},
                selection.linker,
                linker_path(selection.linker, selection),
            )
            active_profile_names.add(profile_name)
            generated = True
        else:
            warn(f"未生成 {profile_name} profile：没有找到完整的编译器、link.exe 和 Windows SDK 环境。")

    remove_stale_compiler_profiles(active_profile_names)
    promote_single_compiler_profile_to_default(active_profile_names)
    return generated


def ensure_conan_profile(check_only: bool) -> bool:
    conan = find_executable("conan")
    if not conan:
        warn("未检测到 conan，跳过 Conan profile 检查。")
        return False

    profile = conan_profiles_dir() / "default"
    if profile.exists():
        ok(f"Conan default profile 已存在：{profile}")
    else:
        log("提示：Conan default profile 不存在（已跳过自动生成，如需请手动运行 conan profile detect）。")
    return True


def ensure_tool(tool: Tool, uv: str | None, check_only: bool) -> bool:
    exe = find_executable(tool.command)
    if exe:
        ok(f"{tool.command} 已安装：{command_version(tool.command)}")
        return True

    warn(f"缺失 {tool.command}：{tool.description}")

    installed = False
    if uv and tool.uv_package:
        installed = install_with_uv(tool, uv, check_only)
    if not installed and tool.windows_winget_id:
        installed = install_with_winget(tool, check_only)

    if installed and find_executable(tool.command):
        ok(f"{tool.command} 安装后已可用：{command_version(tool.command)}")
        return True

    if tool.required:
        error(f"{tool.command} 仍不可用。{tool.manual_hint}")
    else:
        warn(f"{tool.command} 仍不可用；该工具为可选项。{tool.manual_hint}")
    return False


def setup_environment(
    check_only: bool = False,
    *,
    compiler: str = "auto",
    stdlib: str = "auto",
    linker: str = "auto",
    interactive: bool = False,
    selection: ToolchainSelection | None = None,
    install_toolchains: tuple[str, ...] = (),
    toolchain_versions: dict[str, str] | None = None,
    upgrade_toolchains: bool = False,
    llvm_variant: str = "auto",
    input_fn=input,
) -> bool:
    global LAST_AVAILABLE_PRESET_GROUPS, LAST_PRESET_ENVIRONMENTS, LAST_TOOLCHAIN_SELECTION

    log(f"当前系统：{platform.system()} {platform.release()} ({platform.machine()})")
    log(f"Python：{sys.version.split()[0]}，路径：{sys.executable}")

    LAST_TOOLCHAIN_SELECTION = None
    PREFERRED_TOOLCHAIN_BINS.clear()
    PREFERRED_COMPILER_PAIRS.clear()
    PREFERRED_LINKERS.clear()
    try:
        selection = selection or resolve_toolchain_selection(
            compiler=compiler,
            stdlib=stdlib,
            linker=linker,
            interactive=interactive,
            input_fn=input_fn,
        )
        explicit_cli_management = bool(install_toolchains or toolchain_versions or upgrade_toolchains)
        if interactive and not check_only and not explicit_cli_management:
            install_requests = prompt_toolchain_install_requests(selection, input_fn=input_fn)
            explicit_management = True
        elif explicit_cli_management:
            install_requests = noninteractive_toolchain_install_requests(
                selection,
                install_toolchains,
                toolchain_versions,
                upgrade=upgrade_toolchains,
                llvm_variant=llvm_variant,
            )
            explicit_management = True
        else:
            install_requests = ()
            explicit_management = False
    except ValueError as exc:
        error(str(exc))
        return False
    LAST_TOOLCHAIN_SELECTION = selection

    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    if check_only:
        if path_contains(LOCAL_BIN):
            ok(f"当前 PATH 已包含：{LOCAL_BIN}")
        else:
            warn(f"当前 PATH 未包含：{LOCAL_BIN}")
        if is_windows() and UV_INSTALL_DIR.exists():
            if path_contains(UV_INSTALL_DIR):
                ok(f"当前 PATH 已包含：{UV_INSTALL_DIR}")
            else:
                warn(f"当前 PATH 未包含：{UV_INSTALL_DIR}")
    else:
        persist_path(LOCAL_BIN)
        if is_windows() and UV_INSTALL_DIR.exists():
            persist_path(UV_INSTALL_DIR)

    uv = install_uv(check_only)
    required_ok = True

    if not uv:
        required_ok = False
    elif not install_uv_tools.ensure_global_uv_tools(uv, check_only=check_only):
        required_ok = False

    for tool in TOOLS:
        if not ensure_tool(tool, uv, check_only) and tool.required:
            required_ok = False

    if install_requests and not install_toolchain_requests(install_requests, check_only):
        required_ok = False

    if not prepare_selected_toolchain(
        selection,
        check_only,
        allow_compiler_install=not explicit_management,
    ):
        required_ok = False

    if not ensure_conan_profile(check_only):
        required_ok = False

    if not check_only and not generate_compiler_profiles(selection):
        warn("没有生成任何编译器专用 Conan profile。")

    LAST_AVAILABLE_PRESET_GROUPS = available_cmake_preset_groups(selection=selection)
    LAST_PRESET_ENVIRONMENTS = cmake_preset_environments(selection=selection)

    if required_ok:
        ok("必需环境检查通过。")
    else:
        warn("必需环境尚未完全就绪，请根据上面的手动安装提示处理。")
    return required_ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查并安装 C/C++ 模板开发环境。")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只检查环境并打印缺失项，不执行安装。",
    )
    parser.add_argument("--compiler", choices=("auto", "clang", "gcc", "msvc"), default="auto")
    parser.add_argument("--stdlib", choices=("auto", "libc++", "libstdc++", "msvc"), default="auto")
    parser.add_argument("--linker", choices=("auto", "system", "lld", "bfd", "mold", "msvc"), default="auto")
    parser.add_argument(
        "--install-toolchain",
        action="append",
        choices=("gcc", "llvm", "msvc"),
        default=[],
        help="显式安装/管理工具链，可重复指定。交互模式下不指定时会逐项询问。",
    )
    parser.add_argument(
        "--toolchain-version",
        action="append",
        default=[],
        metavar="NAME=VERSION",
        help="指定工具链版本，例如 llvm=22.1.8；可重复指定。",
    )
    parser.add_argument("--upgrade-toolchains", action="store_true", help="允许升级所选的现有工具链。")
    parser.add_argument(
        "--llvm-variant",
        choices=("auto", "mingw", "msvc"),
        default="auto",
        help="Windows LLVM 运行模式；auto 根据标准库选择。",
    )
    parser.add_argument("--non-interactive", action="store_true", help="不显示选择菜单，对 auto 项使用系统默认值。")
    args = parser.parse_args(argv)
    try:
        toolchain_versions = parse_toolchain_versions(args.toolchain_version)
    except ValueError as exc:
        parser.error(str(exc))
    interactive = not args.non_interactive and sys.stdin.isatty()
    return (
        0
        if setup_environment(
            check_only=args.check_only,
            compiler=args.compiler,
            stdlib=args.stdlib,
            linker=args.linker,
            interactive=interactive,
            install_toolchains=tuple(args.install_toolchain),
            toolchain_versions=toolchain_versions,
            upgrade_toolchains=args.upgrade_toolchains,
            llvm_variant=args.llvm_variant,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
