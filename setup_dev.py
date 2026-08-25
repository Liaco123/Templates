import argparse
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
CONAN_CPPSTD = "23"
CONAN_CMAKE_GENERATOR = "Ninja"
DEFAULT_MSVC_VERSION = "193"
MANAGED_PROFILE_NAMES = {"gcc", "clang", "msvc", "clang_msvc"}

COMPILER_LABELS = {
    "clang": "Clang/LLVM",
    "gcc": "GCC",
    "msvc": "Microsoft MSVC",
}
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
            destination.write_bytes(response.read())
    except OSError as exc:
        warn(f"下载失败：{exc}")
        return False
    ok(f"下载完成：{destination}")
    return True


def fetch_text(url: str) -> str | None:
    log(f"读取下载页面：{url}")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "templates-setup"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        warn(f"读取页面失败：{exc}")
        return None


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


def download_and_install_zip(url: str, install_dir: Path, executable: str) -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        archive = Path(temp_dir) / Path(urllib.parse.urlparse(url).path).name
        if not download_file(url, archive):
            return False
        return install_zip_to_dir(archive, install_dir, executable)


def latest_winlibs_url() -> str | None:
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


def find_gcc_pair() -> tuple[str, str] | None:
    exe_suffix = ".exe" if is_windows() else ""
    candidates: list[tuple[Path, Path]] = []

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

    path_clang = find_from_path(f"clang{exe_suffix}")
    path_clangxx = find_from_path(f"clang++{exe_suffix}")
    if path_clang and path_clangxx:
        candidates.append((path_clang, path_clangxx))

    for bin_dir in homebrew_formula_bin_dirs("llvm"):
        candidates.append((bin_dir / "clang", bin_dir / "clang++"))

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


def install_system_packages(packages: tuple[str, ...]) -> bool:
    manager = detect_package_manager()
    if not manager:
        warn("未识别可用的系统包管理器。")
        return False

    unique_packages = tuple(dict.fromkeys(packages))
    if manager == "brew":
        return run_command([shutil.which("brew") or "brew", "install", *unique_packages]).returncode == 0
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


def install_gcc(check_only: bool) -> bool:
    if find_gcc_pair():
        ok("已检测到 GCC/G++。")
        return True

    notes = compiler_install_notes()
    warn(f"未检测到 GCC/G++。{notes['gcc']}")
    if check_only:
        return False

    if is_windows():
        url = latest_winlibs_url()
        if not url:
            warn(f"未能从 winlibs.com 自动解析 GCC 下载链接。请手动下载 WinLibs 并解压到：{GCC_DIR}")
            return False
        if download_and_install_zip(url, GCC_DIR, "g++.exe") and find_gcc_pair():
            ok("GCC/G++ 已通过 WinLibs 安装。")
            return True
        warn(f"GCC/G++ 自动安装失败。请手动下载 WinLibs 并解压到：{GCC_DIR}")
        return False

    manager = detect_package_manager()
    packages = packages_for("gcc", manager) if manager else ()
    if not packages:
        warn("未识别可自动安装 GCC 的包管理器。")
        return False
    return install_system_packages(packages) and bool(find_gcc_pair())


def install_clang(check_only: bool) -> bool:
    if find_clang_pair():
        ok("已检测到 Clang/Clang++。")
        return True

    notes = compiler_install_notes()
    warn(f"未检测到 Clang。{notes['clang']}")
    if check_only:
        return False

    if is_windows():
        url = latest_llvm_mingw_url()
        if not url:
            warn(
                f"未能从 mstorsjo/llvm-mingw 自动解析 Clang 下载链接。请手动下载 ucrt-x86_64 zip 并解压到：{CLANG_DIR}"
            )
            return False
        if download_and_install_zip(url, CLANG_DIR, "clang++.exe") and find_clang_pair():
            ok("Clang/Clang++ 已通过 llvm-mingw 安装。")
            return True
        warn(f"Clang/Clang++ 自动安装失败。请手动下载 llvm-mingw 并解压到：{CLANG_DIR}")
        return False

    if normalized_system() == "darwin":
        manager = detect_package_manager()
        if manager == "brew":
            return install_system_packages(packages_for("clang", manager)) and bool(find_clang_pair())
        warn("未检测到 Xcode Command Line Tools 或 Homebrew；请运行 xcode-select --install 后重试。")
        return False

    manager = detect_package_manager()
    packages = packages_for("clang", manager) if manager else ()
    if not packages:
        warn("未识别可自动安装 Clang 的包管理器。")
        return False
    return install_system_packages(packages) and bool(find_clang_pair())


def install_clang_msvc(check_only: bool) -> bool:
    if not is_windows():
        return True
    if find_clang_cl():
        ok("已检测到 clang-cl。")
        return True

    notes = compiler_install_notes()
    warn(f"未检测到 clang-cl。{notes['clang_msvc']}")
    if check_only:
        return False

    url = latest_llvm_msvc_url()
    if not url:
        warn(f"未能从 llvm/llvm-project release 自动解析 LLVM Windows 安装器。请手动安装到：{CLANG_MSVC_DIR}")
        return False

    with tempfile.TemporaryDirectory() as temp_dir:
        installer = Path(temp_dir) / Path(urllib.parse.urlparse(url).path).name
        if not download_file(url, installer):
            return False
        CLANG_MSVC_DIR.parent.mkdir(parents=True, exist_ok=True)
        result = run_command([str(installer), "/S", f"/D={CLANG_MSVC_DIR}"])
        if result.returncode == 0 and find_clang_cl():
            ok(f"clang-cl 已通过 llvm/llvm-project release 安装到：{CLANG_MSVC_DIR}")
            return True

    warn(f"clang-cl 自动安装失败。请从 llvm/llvm-project releases 下载 Windows installer，并安装到：{CLANG_MSVC_DIR}")
    return False


def install_msvc(check_only: bool) -> bool:
    if not is_windows():
        warn("MSVC 仅支持 Windows。")
        return False
    if has_msvc_build_environment():
        ok("已检测到 MSVC 编译器、link.exe 和 Windows SDK。")
        return True

    warn(f"未检测到完整 MSVC 构建环境。{compiler_install_notes()['msvc']}")
    if check_only:
        return False

    winget = shutil.which("winget")
    if not winget:
        warn("未检测到 winget，无法自动安装 Visual Studio Build Tools。")
        return False
    result = run_command(
        [
            winget,
            "install",
            "--id",
            "Microsoft.VisualStudio.BuildTools",
            "-e",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--override",
            "--passive --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended",
        ]
    )
    if result.returncode == 0 and has_msvc_build_environment():
        ok("MSVC、link.exe 和 Windows SDK 已安装。")
        return True
    warn(
        "Visual Studio Build Tools 安装后仍未发现完整 C++ workload；请在 Visual Studio Installer 中添加 C++ Build Tools。"
    )
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


def prepare_selected_toolchain(selection: ToolchainSelection, check_only: bool) -> bool:
    if not check_only and not is_windows():
        manager = detect_package_manager()
        if manager:
            packages = planned_system_packages(
                selection,
                manager,
                compiler_missing=selected_compiler_executables(selection) is None,
                stdlib_missing=not supports_standard_library(selection),
                linker_missing=linker_path(selection.linker, selection) is None,
            )
            if packages:
                log(f"按 {manager} 合并安装所选工具链软件包：{', '.join(packages)}")
                install_system_packages(packages)

    compiler_ok = install_selected_compiler(selection, check_only)
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
) -> bool:
    global LAST_AVAILABLE_PRESET_GROUPS, LAST_PRESET_ENVIRONMENTS, LAST_TOOLCHAIN_SELECTION

    log(f"当前系统：{platform.system()} {platform.release()} ({platform.machine()})")
    log(f"Python：{sys.version.split()[0]}，路径：{sys.executable}")

    LAST_TOOLCHAIN_SELECTION = None
    try:
        selection = selection or resolve_toolchain_selection(
            compiler=compiler,
            stdlib=stdlib,
            linker=linker,
            interactive=interactive,
        )
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

    if not prepare_selected_toolchain(selection, check_only):
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
    parser.add_argument("--non-interactive", action="store_true", help="不显示选择菜单，对 auto 项使用系统默认值。")
    args = parser.parse_args(argv)
    interactive = not args.non_interactive and sys.stdin.isatty()
    return (
        0
        if setup_environment(
            check_only=args.check_only,
            compiler=args.compiler,
            stdlib=args.stdlib,
            linker=args.linker,
            interactive=interactive,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
