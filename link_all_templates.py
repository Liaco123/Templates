import os
import sys
import subprocess

# --- 配置区域 ---
SOURCE_ROOT = r"D:\MyCode\Templates"
# 目标路径：用户目录/.conan2/templates/command/new
TARGET_REL_PATH = os.path.join(".conan2", "templates", "command", "new")

def log(msg):
    print(f"\033[1;32m[OK] {msg}\033[0m")

def warn(msg):
    print(f"\033[1;33m[SKIP] {msg}\033[0m")

def info(msg):
    print(f"\033[1;36m[INFO] {msg}\033[0m")

def main():
    print("========================================")
    print("   Conan 2 批量模板链接工具")
    print("========================================")

    # 1. 准备路径
    user_home = os.path.expanduser("~")
    target_root = os.path.join(user_home, TARGET_REL_PATH)

    # 2. 检查源目录
    if not os.path.exists(SOURCE_ROOT):
        print(f"\033[1;31m[ERROR] 源目录不存在：{SOURCE_ROOT}\033[0m")
        sys.exit(1)

    # 3. 确保目标父目录存在
    if not os.path.exists(target_root):
        info(f"创建目标目录：{target_root}")
        os.makedirs(target_root)

    print(f"源目录：{SOURCE_ROOT}")
    print(f"目标目录：{target_root}")
    print("-" * 40)

    # 4. 遍历源目录 (os.listdir 不会递归，只看第一层)
    items = os.listdir(SOURCE_ROOT)
    count = 0

    for item_name in items:
        source_item_path = os.path.join(SOURCE_ROOT, item_name)
        target_item_path = os.path.join(target_root, item_name)

        # --- 过滤规则 ---
        
        # 规则 1: 忽略以 . 开头的 (如 .git, .vscode)
        if item_name.startswith("."):
            warn(f"忽略隐藏项：{item_name}")
            continue
        
        # 规则 2: 忽略文件，只处理文件夹
        if not os.path.isdir(source_item_path):
            warn(f"忽略文件：{item_name}")
            continue

        # --- 执行链接 ---
        
        # 清理旧链接 (如果目标已存在)
        if os.path.exists(target_item_path):
            try:
                # 这是一个 Junction 或空目录，直接 rmdir 即可
                os.rmdir(target_item_path)
                info(f"清理旧链接：{item_name}")
            except OSError:
                print(f"\033[1;31m[FAIL] 无法删除旧目标 '{item_name}'，它可能是一个非空真实目录而非链接。\033[0m")
                continue

        # 创建 Junction
        cmd = f'mklink /J "{target_item_path}" "{source_item_path}"'
        
        try:
            # stdout=subprocess.DEVNULL 用于静默执行，不打印 mklink 的默认输出
            subprocess.check_call(cmd, shell=True, stdout=subprocess.DEVNULL)
            log(f"已链接：{item_name}  <==>  {source_item_path}")
            count += 1
        except subprocess.CalledProcessError:
            print(f"\033[1;31m[FAIL] 创建链接失败：{item_name}\033[0m")

    print("========================================")
    print(f"处理完成。共创建了 {count} 个模板链接。")
    print("现在你可以使用 'conan new <模板名> ...' 了。")

if __name__ == "__main__":
    main()