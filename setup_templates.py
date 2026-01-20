import os
import platform
import subprocess
import sys
from pathlib import Path


def setup_templates():
    # 1. 获取源目录（当前脚本所在目录）和目标目录
    source_root = Path(__file__).parent.absolute()
    dest_root = Path("~/.conan2/templates/command/new").expanduser().absolute()

    current_os = platform.system().lower()
    is_windows = current_os == "windows"

    print(f"[*] 检测到操作系统：{platform.system()}")
    print(f"[*] 源目录：{source_root}")
    print(f"[*] 目标目录：{dest_root}")

    # 2. 如果目标根目录不存在，自动创建
    if not dest_root.exists():
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
            print(f"[+] 创建目标根目录成功：{dest_root}")
        except Exception as e:
            print(f"[!] 无法创建目标目录：{e}")
            sys.exit(1)

    # 3. 遍历当前目录下的文件夹
    count = 0
    for item in source_root.iterdir():
        # 过滤掉不需要的项目：
        # 1. 不是文件夹
        # 2. 隐藏文件夹 (.git, .vscode 等)
        # 3. Python 缓存 (__pycache__)
        if not item.is_dir() or item.name.startswith(".") or item.name == "__pycache__":
            continue

        target_path = dest_root / item.name

        # 检查目标是否已存在
        if target_path.exists() or target_path.is_symlink():
            print(f"[-] 跳过：{item.name} (目标已存在)")
            continue

        try:
            if is_windows:
                # --- Windows 策略：使用 mklink /J (Junction) ---
                # mklink 是 cmd 的内部命令，必须用 shell=True
                # /J 只能用于目录，且不需要管理员权限
                # 注意引号，防止路径中有空格
                cmd = f'mklink /J "{target_path}" "{item}"'

                # subprocess.run 会阻塞直到命令完成
                result = subprocess.run(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                if result.returncode == 0:
                    print(f"[+] Win Junction 创建成功：{item.name}")
                    count += 1
                else:
                    print(
                        f"[!] Win Junction 创建失败 {item.name}: {result.stderr.strip()}"
                    )

            else:
                # --- Linux 策略：使用 os.symlink (等同于 ln -s) ---
                # os.symlink(src, dst)
                os.symlink(item, target_path)
                print(f"[+] Linux Symlink 创建成功：{item.name}")
                count += 1

        except Exception as e:
            print(f"[!] 发生错误 {item.name}: {e}")

    print(f"\n[*] 全部完成，共链接了 {count} 个模板目录。")
    print("[*] 你可以使用 'conan new <folder_name>' 进行验证。")


if __name__ == "__main__":
    setup_templates()
