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


HOME = Path.home()
LOCAL_BIN = HOME / ".local" / "bin"
UV_INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "uv" / "bin"
DEV_ROOT = Path("C:/dev") if platform.system().lower() == "windows" else HOME / "dev"
GCC_DIR = DEV_ROOT / "gcc"
CLANG_DIR = DEV_ROOT / "llvm-mingw"
CLANG_MSVC_DIR = DEV_ROOT / "clang_msvc"


@dataclass(frozen=True)
class Tool:
    command: str
    description: str
    required: bool
    uv_package: str | None = None
    windows_winget_id: str | None = None
    manual_hint: str | None = None


TOOLS = [
    Tool("git", "版本控制工具", True, windows_winget_id="Git.Git", manual_hint="安装 Git: https://git-scm.com/downloads"),
    Tool("conan", "C/C++ 包管理器", True, uv_package="conan", manual_hint="安装 uv 后运行: uv tool install conan"),
    Tool("cmake", "CMake 构建系统", True, uv_package="cmake", manual_hint="安装 uv 后运行: uv tool install cmake"),
    Tool("ninja", "Ninja 构建器", True, uv_package="ninja", manual_hint="安装 uv 后运行: uv tool install ninja"),
    Tool("ruff", "Python 代码检查工具", False, uv_package="ruff", manual_hint="安装 uv 后运行: uv tool install ruff"),
    Tool("cppcheck", "C/C++ 静态分析工具", False, windows_winget_id="Cppcheck.Cppcheck", manual_hint="Windows 可安装 Cppcheck；Linux/macOS 使用系统包管理器安装 cppcheck"),
    Tool("sccache", "编译缓存工具", False, manual_hint="安装 sccache: https://github.com/mozilla/sccache/releases"),
]


def compiler_install_notes() -> dict[str, str]:
    if is_windows():
        return {
            "gcc": f"自动安装来源：https://winlibs.com/；目标目录：{GCC_DIR}",
            "clang": f"自动安装来源：https://github.com/mstorsjo/llvm-mingw/releases；目标目录：{CLANG_DIR}",
            "clang_msvc": f"自动安装来源：https://releases.llvm.org/；目标目录：{CLANG_MSVC_DIR}",
        }
    if platform.system().lower() == "darwin":
        return {
            "gcc": "如已安装 Homebrew，可自动执行 brew install gcc；否则请先安装 Homebrew。",
            "clang": "通常由 Xcode Command Line Tools 提供；如缺失，请手动运行 xcode-select --install。",
        }
    return {
        "gcc": "Linux 下优先使用 apt/dnf/pacman 安装 gcc/g++。",
        "clang": "Linux 下优先使用 apt/dnf/pacman 安装 clang。",
        "clang_msvc": "clang_msvc 只在 Windows/MSVC 工具链下生成。",
    }


def is_windows() -> bool:
    return platform.system().lower() == "windows"


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
    html = fetch_text("https://releases.llvm.org/download.html")
    if not html:
        return None
    candidates = []
    for href in re.findall(r'href=["\']([^"\']*LLVM-[^"\']*-win64\.exe)["\']', html, flags=re.IGNORECASE):
        candidates.append(urllib.parse.urljoin("https://releases.llvm.org/download.html", href))
    return candidates[0] if candidates else None


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
            "$target = "
            + repr(path_str)
            + "; "
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
    candidates = [LOCAL_BIN / (f"{command}.exe" if is_windows() else command)]
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


def find_gcc_pair() -> tuple[str, str] | None:
    exe_suffix = ".exe" if is_windows() else ""
    candidates: list[tuple[Path, Path]] = []

    path_gcc = find_from_path(f"gcc{exe_suffix}")
    path_gxx = find_from_path(f"g++{exe_suffix}")
    if path_gcc and path_gxx:
        candidates.append((path_gcc, path_gxx))

    for gxx in find_in_roots({f"g++{exe_suffix}"}):
        gcc = gxx.with_name(f"gcc{exe_suffix}")
        candidates.append((gcc, gxx))

    for gcc, gxx in candidates:
        if gcc.exists() and gxx.exists():
            return str(gcc), str(gxx)
    return None


def find_clang_pair() -> tuple[str, str] | None:
    exe_suffix = ".exe" if is_windows() else ""
    candidates: list[tuple[Path, Path]] = []

    path_clang = find_from_path(f"clang{exe_suffix}")
    path_clangxx = find_from_path(f"clang++{exe_suffix}")
    if path_clang and path_clangxx:
        candidates.append((path_clang, path_clangxx))

    for clangxx in find_in_roots({f"clang++{exe_suffix}"}):
        if is_windows() and (clangxx.parent / "clang-cl.exe").exists():
            continue
        clang = clangxx.with_name(f"clang{exe_suffix}")
        candidates.append((clang, clangxx))

    for clang, clangxx in candidates:
        if is_windows() and (clangxx.parent / "clang-cl.exe").exists():
            continue
        if clang.exists() and clangxx.exists():
            return str(clang), str(clangxx)
    return None


def find_clang_cl() -> str | None:
    exe_suffix = ".exe" if is_windows() else ""
    path_clang_cl = find_from_path(f"clang-cl{exe_suffix}")
    if path_clang_cl:
        return str(path_clang_cl)

    candidates = find_in_roots({f"clang-cl{exe_suffix}"})
    return str(candidates[0]) if candidates else None


def discovered_compilers() -> dict[str, str | tuple[str, str]]:
    compilers: dict[str, str | tuple[str, str]] = {}
    gcc = find_gcc_pair()
    if gcc:
        compilers["gcc"] = gcc
    clang = find_clang_pair()
    if clang:
        compilers["clang"] = clang
    clang_cl = find_clang_cl()
    if clang_cl:
        compilers["clang_msvc"] = clang_cl
    return compilers


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

    if platform.system().lower() == "darwin":
        brew = shutil.which("brew")
        if not brew:
            warn("未检测到 Homebrew，无法自动安装 GCC。")
            return False
        return run_command([brew, "install", "gcc"]).returncode == 0 and bool(find_gcc_pair())

    if shutil.which("apt"):
        return run_command(["sudo", "apt", "update"]).returncode == 0 and run_command(["sudo", "apt", "install", "-y", "gcc", "g++"]).returncode == 0
    if shutil.which("dnf"):
        return run_command(["sudo", "dnf", "install", "-y", "gcc", "gcc-c++"]).returncode == 0
    if shutil.which("pacman"):
        return run_command(["sudo", "pacman", "-S", "--needed", "--noconfirm", "gcc"]).returncode == 0

    warn("未识别可自动安装 GCC 的包管理器。")
    return False


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
            warn(f"未能从 mstorsjo/llvm-mingw 自动解析 Clang 下载链接。请手动下载 ucrt-x86_64 zip 并解压到：{CLANG_DIR}")
            return False
        if download_and_install_zip(url, CLANG_DIR, "clang++.exe") and find_clang_pair():
            ok("Clang/Clang++ 已通过 llvm-mingw 安装。")
            return True
        warn(f"Clang/Clang++ 自动安装失败。请手动下载 llvm-mingw 并解压到：{CLANG_DIR}")
        return False

    if platform.system().lower() == "darwin":
        warn("macOS 的 Clang 通常通过 Xcode Command Line Tools 安装，脚本不强制启动 GUI 安装器。")
        return False

    if shutil.which("apt"):
        return run_command(["sudo", "apt", "update"]).returncode == 0 and run_command(["sudo", "apt", "install", "-y", "clang"]).returncode == 0
    if shutil.which("dnf"):
        return run_command(["sudo", "dnf", "install", "-y", "clang"]).returncode == 0
    if shutil.which("pacman"):
        return run_command(["sudo", "pacman", "-S", "--needed", "--noconfirm", "clang"]).returncode == 0

    warn("未识别可自动安装 Clang 的包管理器。")
    return False


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
        warn(f"未能从 releases.llvm.org 自动解析 LLVM Windows 安装器。请手动安装到：{CLANG_MSVC_DIR}")
        return False

    with tempfile.TemporaryDirectory() as temp_dir:
        installer = Path(temp_dir) / Path(urllib.parse.urlparse(url).path).name
        if not download_file(url, installer):
            return False
        CLANG_MSVC_DIR.parent.mkdir(parents=True, exist_ok=True)
        result = run_command([str(installer), "/S", f"/D={CLANG_MSVC_DIR}"])
        if result.returncode == 0 and find_clang_cl():
            ok(f"clang-cl 已通过 releases.llvm.org 安装到：{CLANG_MSVC_DIR}")
            return True

    warn(f"clang-cl 自动安装失败。请从 releases.llvm.org 下载 Windows installer，并安装到：{CLANG_MSVC_DIR}")
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

    clang_cl = find_clang_cl()
    if clang_cl:
        ok(f"检测到 clang-cl：{clang_cl}")
        found = True

    cl = find_executable("cl")
    if cl:
        ok(f"检测到 MSVC cl：{cl}")
        found = True

    if found:
        return True

    warn("未检测到 C++ 编译器。Conan/CMake 已可安装，但实际构建仍需要编译器。")
    if is_windows():
        warn("建议安装 Visual Studio Build Tools，勾选 C++ build tools；或安装 LLVM/MinGW 并配置 Conan profile。")
    else:
        warn("请使用系统包管理器安装 clang 或 gcc。")
    return False


def conan_profiles_dir() -> Path:
    return HOME / ".conan2" / "profiles"


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


def write_profile(name: str, settings: dict[str, str], compiler_executables: dict[str, str]) -> None:
    conan_profiles_dir().mkdir(parents=True, exist_ok=True)
    profile = conan_profiles_dir() / name
    lines = ["[settings]"]
    for key in ("os", "arch", "compiler", "compiler.version", "compiler.cppstd", "compiler.runtime", "compiler.runtime_type", "compiler.libcxx", "build_type"):
        if key in settings:
            lines.append(f"{key}={settings[key]}")
    lines.extend(["", "[conf]", f"tools.build:compiler_executables={json.dumps(compiler_executables)}", ""])
    profile.write_text("\n".join(lines), encoding="utf-8")
    ok(f"已生成 Conan profile：{profile}")


def generate_compiler_profiles() -> bool:
    default_settings = read_default_profile_settings()
    system = platform.system()
    arch = default_settings.get("arch", "x86_64")
    generated = False

    gcc_pair = find_gcc_pair()
    if gcc_pair:
        gcc, gxx = gcc_pair
        write_profile(
            "gcc",
            {
                "os": default_settings.get("os", system),
                "arch": arch,
                "compiler": "gcc",
                "compiler.version": compiler_major_version(gxx, "13"),
                "compiler.cppstd": "20",
                "compiler.libcxx": "libstdc++11",
                "build_type": default_settings.get("build_type", "Release"),
            },
            {"c": gcc, "cpp": gxx},
        )
        generated = True
    else:
        warn("未生成 gcc profile：没有找到 gcc/g++ 可执行文件。")

    clang_pair = find_clang_pair()
    if clang_pair:
        clang, clangxx = clang_pair
        write_profile(
            "clang",
            {
                "os": default_settings.get("os", system),
                "arch": arch,
                "compiler": "clang",
                "compiler.version": compiler_major_version(clangxx, "17"),
                "compiler.cppstd": "20",
                "compiler.libcxx": "libc++" if system == "Darwin" else "libstdc++11",
                "build_type": default_settings.get("build_type", "Release"),
            },
            {"c": clang, "cpp": clangxx},
        )
        generated = True
    else:
        warn("未生成 clang profile：没有找到 clang/clang++ 可执行文件。")

    if is_windows():
        clang_cl = find_clang_cl()
        if clang_cl and default_settings.get("compiler") == "msvc":
            settings = dict(default_settings)
            settings["compiler"] = "msvc"
            settings.setdefault("compiler.cppstd", "20")
            write_profile("clang_msvc", settings, {"c": clang_cl, "cpp": clang_cl})
            generated = True
        elif clang_cl:
            warn("检测到 clang-cl，但 default profile 不是 MSVC，无法安全生成 clang_msvc profile。请先安装 MSVC Build Tools 并运行 conan profile detect --force。")
        else:
            warn("未生成 clang_msvc profile：没有找到 clang-cl。")

    return generated


def ensure_conan_profile(check_only: bool) -> bool:
    conan = find_executable("conan")
    if not conan:
        warn("未检测到 conan，跳过 Conan profile 检查。")
        return False

    profile = HOME / ".conan2" / "profiles" / "default"
    if profile.exists():
        ok(f"Conan default profile 已存在：{profile}")
        return True

    warn("未检测到 Conan default profile。")
    if check_only:
        warn("当前为仅检查模式，不会执行 conan profile detect。")
        return False

    result = run_command([conan, "profile", "detect", "--force"])
    if result.returncode == 0:
        ok("已生成 Conan default profile。")
        return True

    warn("Conan default profile 自动生成失败。请手动运行：conan profile detect --force")
    return False


def ensure_tool(tool: Tool, uv: str | None, check_only: bool) -> bool:
    exe = find_executable(tool.command)
    if exe:
        ok(f"{tool.command} 已安装：{command_version(tool.command)}")
        return True

    warn(f"缺失 {tool.command}：{tool.description}")

    installed = False
    if uv and tool.uv_package:
        installed = install_with_uv(tool, uv, check_only)
    elif tool.windows_winget_id:
        installed = install_with_winget(tool, check_only)

    if not installed and tool.windows_winget_id and not tool.uv_package:
        installed = install_with_winget(tool, check_only)

    if installed and find_executable(tool.command):
        ok(f"{tool.command} 安装后已可用：{command_version(tool.command)}")
        return True

    if tool.required:
        error(f"{tool.command} 仍不可用。{tool.manual_hint}")
    else:
        warn(f"{tool.command} 仍不可用；该工具为可选项。{tool.manual_hint}")
    return False


def setup_environment(check_only: bool = False) -> bool:
    log(f"当前系统：{platform.system()} {platform.release()} ({platform.machine()})")
    log(f"Python：{sys.version.split()[0]}，路径：{sys.executable}")

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

    for tool in TOOLS:
        if not ensure_tool(tool, uv, check_only) and tool.required:
            required_ok = False

    compilers = discovered_compilers()
    if compilers:
        ok("已发现至少一个 C/C++ 编译器，跳过编译器自动安装。")
    else:
        gcc_ok = install_gcc(check_only)
        clang_ok = install_clang(check_only)
        clang_msvc_ok = install_clang_msvc(check_only)
        if not gcc_ok or not clang_ok or (is_windows() and not clang_msvc_ok):
            required_ok = False

    if not check_compiler():
        required_ok = False

    if not ensure_conan_profile(check_only):
        required_ok = False

    if not check_only and not generate_compiler_profiles():
        warn("没有生成任何编译器专用 Conan profile。")

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
    args = parser.parse_args(argv)
    return 0 if setup_environment(check_only=args.check_only) else 1


if __name__ == "__main__":
    raise SystemExit(main())
