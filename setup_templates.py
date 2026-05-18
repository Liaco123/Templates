import os
import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


TEMPLATE_NAMES = {"basic_exe", "basic_lib", "header_lib"}


def iter_template_dirs(source_root):
    for item in sorted(source_root.iterdir(), key=lambda path: path.name):
        if item.name in TEMPLATE_NAMES and item.is_dir():
            yield item


def setup_templates(source_root=None, dest_root=None, link=False):
    source_root = Path(source_root or Path(__file__).parent).resolve()
    dest_root = Path(dest_root or "~/.conan2/templates/command/new").expanduser().resolve()

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

    count = 0
    for item in iter_template_dirs(source_root):
        target_path = dest_root / item.name

        if target_path.exists() or target_path.is_symlink():
            print(f"[-] 跳过：{item.name} (目标已存在)")
            continue

        try:
            if not link:
                shutil.copytree(
                    item,
                    target_path,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                )
                print(f"[+] 模板复制成功：{item.name}")
                count += 1
            elif is_windows:
                cmd = f'mklink /J "{target_path}" "{item}"'
                result = subprocess.run(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                if result.returncode == 0:
                    print(f"[+] Windows Junction 创建成功：{item.name}")
                    count += 1
                else:
                    print(
                        f"[!] Windows Junction 创建失败 {item.name}: {result.stderr.strip()}"
                    )

            else:
                os.symlink(item, target_path)
                print(f"[+] Symlink 创建成功：{item.name}")
                count += 1

        except Exception as e:
            print(f"[!] 发生错误 {item.name}: {e}")

    action = "链接" if link else "复制"
    print(f"\n[*] 全部完成，共{action}了 {count} 个模板目录。")
    print("[*] 你可以使用 'conan new <folder_name>' 进行验证。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install local Conan new templates.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).parent,
        help="模板源目录，默认是当前脚本所在目录。",
    )
    parser.add_argument(
        "--dest-root",
        type=Path,
        default=Path("~/.conan2/templates/command/new").expanduser(),
        help="Conan new 模板目标目录。",
    )
    parser.add_argument(
        "--link",
        action="store_true",
        help="使用 symlink/Junction 注册模板；默认复制模板并忽略 Python 缓存文件。",
    )
    args = parser.parse_args(argv)
    setup_templates(args.source_root, args.dest_root, link=args.link)


if __name__ == "__main__":
    main()
