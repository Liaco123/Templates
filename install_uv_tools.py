import argparse
import platform
import subprocess
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LOCK_FILE = PROJECT_ROOT / "uv.lock"
PYTHON_VERSION_FILE = PROJECT_ROOT / ".python-version"
GLOBAL_TOOL_PACKAGES = ("conan", "cmake", "ninja", "ruff")


def locked_tool_requirements(lock_file: Path = LOCK_FILE) -> dict[str, str]:
    with lock_file.open("rb") as handle:
        lock_data = tomllib.load(handle)

    versions: dict[str, set[str]] = {name: set() for name in GLOBAL_TOOL_PACKAGES}
    for package in lock_data.get("package", []):
        name = package.get("name")
        version = package.get("version")
        if name in versions and version:
            versions[name].add(version)

    requirements: dict[str, str] = {}
    for name, found_versions in versions.items():
        if len(found_versions) != 1:
            rendered = ", ".join(sorted(found_versions)) or "missing"
            raise RuntimeError(f"uv.lock must contain exactly one version for {name}; found: {rendered}")
        requirements[name] = f"{name}=={next(iter(found_versions))}"
    return requirements


def requested_python_version(version_file: Path = PYTHON_VERSION_FILE) -> str:
    return version_file.read_text(encoding="utf-8").strip()


def uv_tool_bin_dir(uv: str) -> Path:
    result = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or not result.stdout.strip():
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"cannot resolve uv tool bin directory: {detail}")
    return Path(result.stdout.strip())


def tool_executable(tool_bin_dir: Path, command: str) -> Path:
    suffix = ".exe" if platform.system().lower() == "windows" else ""
    return tool_bin_dir / f"{command}{suffix}"


def verify_global_tools(uv: str, requirements: dict[str, str]) -> bool:
    try:
        bin_dir = uv_tool_bin_dir(uv)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"[错误] 无法检查全局 uv tools：{exc}")
        return False

    all_ok = True
    for name, requirement in requirements.items():
        expected_version = requirement.rsplit("==", 1)[1]
        executable = tool_executable(bin_dir, name)
        try:
            result = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[错误] 全局工具不可用：{name}（{exc}）")
            all_ok = False
            continue

        output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode == 0 and expected_version in output:
            print(f"[完成] 全局 uv tool 已就绪：{name} {expected_version}")
        else:
            print(f"[错误] 全局 uv tool 版本异常：期望 {requirement}，实际 {output or '无法执行'}")
            all_ok = False
    return all_ok


def ensure_global_uv_tools(uv: str, check_only: bool = False) -> bool:
    try:
        requirements = locked_tool_requirements()
        python_version = requested_python_version()
    except (OSError, RuntimeError, tomllib.TOMLDecodeError) as exc:
        print(f"[错误] 无法读取全局 uv tool 锁定信息：{exc}")
        return False

    if not check_only:
        for requirement in requirements.values():
            print(f"[信息] 安装用户级全局 uv tool：{requirement}")
            result = subprocess.run(
                [uv, "tool", "install", "--force", "--python", python_version, requirement],
                text=True,
            )
            if result.returncode != 0:
                print(f"[错误] 全局 uv tool 安装失败：{requirement}")
                return False

    return verify_global_tools(uv, requirements)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install repository-locked tools with uv tool.")
    parser.add_argument("--uv", default="uv", help="Path to the uv executable.")
    parser.add_argument("--check-only", action="store_true", help="Verify without installing tools.")
    args = parser.parse_args(argv)
    return 0 if ensure_global_uv_tools(args.uv, check_only=args.check_only) else 1


if __name__ == "__main__":
    raise SystemExit(main())
