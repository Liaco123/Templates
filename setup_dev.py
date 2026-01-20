import os
import sys
import shutil
import subprocess
import platform
from pathlib import Path

# --- 配置清单 ---
TOOLS_TO_INSTALL = [
    "conan",      # C++ 包管理器
    "cmake",      # 构建系统
    "ninja",      # 极速构建器
    "sccache",    # 编译缓存
    "cppcheck",   # 静态分析
    "ruff",       # Python 代码检查
]

# 定义跨平台的 ~/.local/bin
HOME = Path.home()
LOCAL_BIN = HOME / ".local" / "bin"

# 颜色代码 (仅用于支持 ANSI 的终端)
class Colors:
    GREEN = "\033[1;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[1;31m"
    RESET = "\033[0m"

def log(msg):
    print(f"{Colors.GREEN}[SETUP] {msg}{Colors.RESET}")

def warn(msg):
    print(f"{Colors.YELLOW}[WARN]  {msg}{Colors.RESET}")

def error(msg):
    print(f"{Colors.RED}[ERROR] {msg}{Colors.RESET}")

def is_windows():
    return platform.system().lower() == "windows"

def run(cmd, shell=True, check=True, env=None):
    """执行命令的封装"""
    try:
        # 合并当前环境变量
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
            
        subprocess.run(cmd, shell=shell, check=check, env=run_env)
    except subprocess.CalledProcessError as e:
        error(f"执行失败：{cmd}")
        if not check:
            return False
        sys.exit(1)
    return True

def add_to_path(path_to_add):
    """将路径添加到用户环境变量 (持久化)"""
    path_str = str(path_to_add)
    
    # 1. 检查当前会话是否已有该路径
    if path_str in os.environ["PATH"]:
        log(f"路径已在 PATH 中：{path_str}")
        return

    log(f"正在将路径添加到用户环境变量：{path_str}")

    if is_windows():
        # Windows: 使用 setx 或修改注册表 (这里用 PowerShell 修改用户环境，更安全)
        # 注意：setx 有 1024 字符限制，PowerShell 脚本更稳健
        ps_cmd = (
            f'$oldPath = [Environment]::GetEnvironmentVariable("Path", "User"); '
            f'if ($oldPath -notlike "*{path_str}*") {{ '
            f'[Environment]::SetEnvironmentVariable("Path", "$oldPath;{path_str}", "User"); '
            f'Write-Output "Path updated." }} else {{ Write-Output "Path already exists." }}'
        )
        run(f'powershell -c "{ps_cmd}"')
    else:
        # Linux/macOS: 写入 .bashrc 或 .zshrc
        shell = os.environ.get("SHELL", "/bin/bash")
        rc_file = HOME / ".bashrc"
        if "zsh" in shell:
            rc_file = HOME / ".zshrc"
        
        export_cmd = f'\n# Added by setup script\nexport PATH="{path_str}:$PATH"\n'
        
        try:
            content = rc_file.read_text() if rc_file.exists() else ""
            if path_str not in content:
                with open(rc_file, "a") as f:
                    f.write(export_cmd)
                log(f"已添加到 {rc_file}。请运行 'source {rc_file}' 生效。")
            else:
                log(f"路径配置已存在于 {rc_file}")
        except Exception as e:
            warn(f"无法写入配置文件：{e}")

    # 临时更新当前脚本的 PATH，以便后续立即能找到命令
    os.environ["PATH"] = f"{path_str}{os.pathsep}{os.environ['PATH']}"

def find_executable(name):
    """查找可执行文件，优先查找 LOCAL_BIN"""
    # 1. 先找我们自定义的目录
    target = LOCAL_BIN / (f"{name}.exe" if is_windows() else name)
    if target.exists():
        return str(target)
    
    # 2. 再找系统 PATH
    return shutil.which(name)

def install_uv():
    """安装 uv"""
    uv_path = find_executable("uv")
    if uv_path:
        log(f"uv 已安装：{uv_path}")
        # 尝试更新 uv 自身
        run(f"{uv_path} self update", check=False)
        return uv_path

    log("正在安装 uv...")
    if is_windows():
        cmd = 'powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    else:
        cmd = "curl -LsSf https://astral.sh/uv/install.sh | sh"
    
    run(cmd)
    
    # 安装后重新定位 uv
    uv_path = find_executable("uv")
    # 如果找不到，尝试默认安装路径 (uv 默认装在 ~/.local/bin 或 ~/.cargo/bin)
    if not uv_path:
        # Windows 上安装脚本有时装到 AppData
        win_uv = Path(os.environ.get("LOCALAPPDATA", "")) / "uv" / "bin" / "uv.exe"
        if is_windows() and win_uv.exists():
            return str(win_uv)
        
        # 再次兜底 ~/.local/bin
        local_uv = LOCAL_BIN / ("uv.exe" if is_windows() else "uv")
        if local_uv.exists():
            return str(local_uv)
            
    return uv_path or "uv" # 如果还找不到，寄希望于 path 刷新

def install_git():
    """安装 Git (仅 Windows 尝试，Linux 提示用户)"""
    if shutil.which("git"):
        log("Git 已检测到。")
        return

    log("未检测到 Git，尝试安装...")
    if is_windows():
        if shutil.which("winget"):
            cmd = "winget install --id Git.Git -e --source winget --silent --accept-source-agreements --accept-package-agreements"
            if run(cmd, check=False):
                log("Git 安装成功！")
                return
        warn("Winget 不可用，请手动安装 Git: https://git-scm.com/download/win")
    else:
        # Linux
        warn("请使用系统包管理器安装 git (例如：sudo apt install git)")

def install_tools(uv_exe):
    """使用 uv 安装/更新工具"""
    log("正在检查并安装开发工具链...")
    
    # 确保 ~/.local/bin 存在
    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    
    # 将 ~/.local/bin 加入 PATH (如果不在的话)
    add_to_path(LOCAL_BIN)

    # 这里的 env 确保 uv 知道我们要把工具装到哪里 (uv 默认就是 ~/.local/bin，但显式指定更安全)
    # 注意：uv tool install 默认行为就是安装到用户目录的 bin 下
    
    for tool in TOOLS_TO_INSTALL:
        print(f" -> 处理 {tool}...")
        
        # 检查是否已安装
        # uv tool list 输出比较长，我们可以简单粗暴地用 --force 覆盖安装来实现“安装或更新”
        # 或者先用 upgrade 尝试
        
        # 策略：直接使用 install --force。
        # 理由：如果没装，它会装。如果装了，它会重装到最新版。
        # 相比先 check 再 upgrade，这样更简单且能保证版本最新。
        # 也可以用 uv tool upgrade --all 但那样无法控制列表。
        
        cmd = f'{uv_exe} tool install {tool} --force'
        run(cmd)

def main():
    print("========================================")
    print(f"   开发环境配置 v6.0 ({platform.system()})")
    print("========================================")

    # 1. 确保目录结构
    if not LOCAL_BIN.exists():
        log(f"创建目录：{LOCAL_BIN}")
        LOCAL_BIN.mkdir(parents=True, exist_ok=True)

    # 2. 安装/获取 uv
    uv_exe = install_uv()
    
    # 3. 再次确保 ~/.local/bin 在 path 里 (uv 安装完可能会变)
    add_to_path(LOCAL_BIN)

    # 4. 安装 Git
    install_git()

    # 5. 安装工具链
    install_tools(uv_exe)

    print("\n========================================")
    log("所有任务执行完毕！")
    print("----------------------------------------")
    log(f"工具已安装至：{LOCAL_BIN}")
    
    if is_windows():
        warn("Windows 用户请注意：环境变量已更新，但在当前打开的 CMD/PowerShell 窗口可能不生效。")
        warn("请务必【关闭并重新打开】终端，然后输入 'conan --version' 验证。")
    else:
        warn("Linux 用户请注意：请运行 'source ~/.bashrc' (或 ~/.zshrc) 以更新 PATH。")
    print("========================================")

if __name__ == "__main__":
    main()