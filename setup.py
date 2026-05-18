import argparse
import sys
from datetime import datetime
from pathlib import Path

import setup_dev
import setup_templates


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def default_log_file(log_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return log_dir / f"setup-{timestamp}.log"


def run_setup(args) -> int:
    print("========================================")
    print("  Conan C++ 模板环境初始化")
    print("========================================")
    print(f"[信息] 日志文件：{args.log_file}")
    print(f"[信息] 运行模式：{'仅检查' if args.check_only else '检查并补齐缺失项'}")

    env_ok = True
    if args.skip_env:
        print("[警告] 已跳过开发环境检查。")
    else:
        print("\n[阶段] 1/2 检查并准备开发环境")
        env_ok = setup_dev.setup_environment(check_only=args.check_only)

    if args.skip_templates:
        print("[警告] 已跳过 Conan 模板注册。")
    else:
        print("\n[阶段] 2/2 注册 Conan 模板")
        if args.check_only:
            print("[信息] 仅检查模式不会复制或链接模板。")
        else:
            setup_templates.setup_templates(
                dest_root=args.template_dest,
                link=args.link_templates,
            )

    print("\n========================================")
    if env_ok:
        print("[完成] setup.py 执行完成。")
    else:
        print("[警告] setup.py 执行完成，但环境仍有缺失项；请根据上方手动安装提示处理。")
    print("========================================")
    return 0 if env_ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查并初始化 Conan C++ 模板开发环境。")
    parser.add_argument("--check-only", action="store_true", help="只检查环境，不安装工具，不注册模板。")
    parser.add_argument("--skip-env", action="store_true", help="跳过开发环境检查和安装。")
    parser.add_argument("--skip-templates", action="store_true", help="跳过 Conan 模板注册。")
    parser.add_argument("--link-templates", action="store_true", help="用 symlink/Junction 注册模板；默认复制模板。")
    parser.add_argument(
        "--template-dest",
        type=Path,
        default=Path("~/.conan2/templates/command/new").expanduser(),
        help="Conan new 模板目标目录。",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="日志目录，默认写入当前项目的 logs 目录。",
    )
    args = parser.parse_args(argv)

    args.log_dir.mkdir(parents=True, exist_ok=True)
    args.log_file = default_log_file(args.log_dir)

    with args.log_file.open("w", encoding="utf-8") as log_handle:
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = Tee(original_stdout, log_handle)
        sys.stderr = Tee(original_stderr, log_handle)
        try:
            return run_setup(args)
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


if __name__ == "__main__":
    raise SystemExit(main())
