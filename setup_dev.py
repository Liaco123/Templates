import os
import shutil
import subprocess
import sys

# --- 工具清单 ---
TOOLS_TO_INSTALL = [
    "conan",  # 包管理器
    "cmake",  # 构建系统
    "ninja",  # 极速构建器
    "sccache",  # 编译缓存
    "cppcheck",  # 静态分析
    "ruff",  # Python 代码检查
]


def log(msg):
    print(f"\033[1;32m[SETUP] {msg}\033[0m")


def warn(msg):
    print(f"\033[1;33m[WARN]  {msg}\033[0m")


def error(msg):
    print(f"\033[1;31m[ERROR] {msg}\033[0m")


def run(cmd, shell=True, check=True):
    try:
        subprocess.run(cmd, shell=shell, check=check)
    except subprocess.CalledProcessError:
        print(f"\033[1;31m[ERROR] 执行失败：{cmd}\033[0m")
        if "winget" in cmd:
            return False
        sys.exit(1)
    return True


def get_uv_path():
    """寻找 uv.exe 的绝对路径，解决环境变量未刷新问题"""
    # 1. 如果 PATH 里已经有了 (比如你重启过终端)，直接用
    if shutil.which("uv"):
        return "uv"

    # 2. 如果 PATH 里没有，去刚才安装的目录找
    home = os.path.expanduser("~")
    # uv 在 Windows 上的常见安装位置
    possible_paths = [
        os.path.join(home, ".local", "bin", "uv.exe"),  # 新版默认
        os.path.join(home, ".cargo", "bin", "uv.exe"),  # 旧版/Rust 习惯
        os.path.join(home, "AppData", "Local", "uv", "bin", "uv.exe"),
    ]

    for p in possible_paths:
        if os.path.exists(p):
            return f'"{p}"'  # 加引号防止路径带空格

    return None


def install_uv():
    """安装 uv"""
    if get_uv_path():
        log("uv 已存在，跳过安装。")
        return

    log("正在安装 uv...")
    # 增加 -ExecutionPolicy Bypass 绕过权限限制
    cmd = 'powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    run(cmd)


def install_git():
    """尝试安装 Git"""
    if shutil.which("git"):
        log("Git 已检测到。")
        return

    log("正在尝试安装 Git...")
    if shutil.which("winget"):
        cmd = "winget install --id Git.Git -e --source winget --silent --accept-source-agreements --accept-package-agreements"
        if run(cmd, check=False):
            log("Git 安装成功！")
            return

    # 如果 Winget 失败或不存在
    warn("无法自动安装 Git (未找到 Winget)。")
    print("   [必须] 请手动下载安装 Git: https://git-scm.com/download/win")
    print("   安装时请一路 Next 即可。")


def install_tools_via_uv():
    """使用绝对路径调用 uv 安装工具"""
    log("正在安装开发工具链...")

    # 获取 uv 的绝对路径
    uv_exe = get_uv_path()
    if not uv_exe:
        error("找不到 uv.exe！请尝试重启终端后再次运行此脚本。")
        sys.exit(1)

    print(f"   Using uv at: {uv_exe}")

    for tool in TOOLS_TO_INSTALL:
        print(f" -> 处理 {tool}...")
        # 使用绝对路径调用 uv
        cmd = f"{uv_exe} tool install --force {tool}"
        run(cmd)


def main():
    print("========================================")
    print("   C++ / Robotics 开发环境配置 v5.0")
    print("========================================")

    install_uv()
    install_git()
    install_tools_via_uv()

    print("\n========================================")
    log("脚本执行完毕！")
    print("----------------------------------------")
    warn("【重要操作】")
    print("1. 你的系统没有 Winget，请务必手动安装 Git！")
    print("2. 必须 关闭并重启 这个终端窗口，新装的 uv、cmake、ninja 才能生效。")
    print("========================================")


if __name__ == "__main__":
    main()
