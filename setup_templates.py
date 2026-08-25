import os
import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


TEMPLATE_NAMES = {"basic_exe", "basic_lib", "header_lib"}
PRESET_GROUPS = ("clang-msvc", "msvc", "clang-libcxx", "clang-std", "gcc")
GENERATED_TEMPLATE_ROOT = Path(".generated") / "conan_new_templates"
CONAN_HOME = Path(os.environ.get("CONAN_HOME", Path.home() / ".conan2")).expanduser()


def iter_template_dirs(source_root):
    for item in sorted(source_root.iterdir(), key=lambda path: path.name):
        if item.name in TEMPLATE_NAMES and item.is_dir():
            yield item


def preset_group(name):
    for group in PRESET_GROUPS:
        if name.startswith(f"{group}-"):
            return group
    return None


def filter_cmake_presets(template_dir, enabled_preset_groups):
    if enabled_preset_groups is None:
        return

    preset_file = template_dir / "CMakePresets.json"
    if not preset_file.exists():
        return

    try:
        data = json.loads(preset_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[!] 无法解析 {preset_file}，跳过 preset 过滤：{exc}")
        return

    configure_presets = []
    kept_configure_names = set()
    for preset in data.get("configurePresets", []):
        name = preset.get("name", "")
        group = preset_group(name)
        if group and group not in enabled_preset_groups:
            continue
        configure_presets.append(preset)
        kept_configure_names.add(name)
    data["configurePresets"] = configure_presets

    for key in ("buildPresets", "testPresets"):
        filtered = []
        for preset in data.get(key, []):
            configure_preset = preset.get("configurePreset")
            if configure_preset and configure_preset not in kept_configure_names:
                continue
            filtered.append(preset)
        data[key] = filtered

    preset_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    groups = ", ".join(sorted(enabled_preset_groups)) if enabled_preset_groups else "无"
    print(f"[+] 已按可用工具链过滤 CMakePresets：{template_dir.name} ({groups})")


def apply_preset_environments(template_dir, preset_environments):
    if not preset_environments:
        return

    preset_file = template_dir / "CMakePresets.json"
    if not preset_file.exists():
        return

    try:
        data = json.loads(preset_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[!] 无法解析 {preset_file}，跳过 preset 环境写入：{exc}")
        return

    changed = False
    for preset in data.get("configurePresets", []):
        group = preset_group(preset.get("name", ""))
        environment = preset_environments.get(group)
        if environment:
            preset["environment"] = environment
            changed = True

    if changed:
        preset_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[+] 已写入 CMake preset 环境：{template_dir.name}")


def prepare_generated_templates(source_root, enabled_preset_groups, preset_environments=None):
    if enabled_preset_groups is None:
        return source_root

    generated_root = source_root / GENERATED_TEMPLATE_ROOT
    if generated_root.exists():
        shutil.rmtree(generated_root)
    generated_root.mkdir(parents=True, exist_ok=True)

    root_clang_format = source_root / ".clang-format"
    for item in iter_template_dirs(source_root):
        target = generated_root / item.name
        shutil.copytree(
            item,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        if root_clang_format.exists():
            shutil.copy2(root_clang_format, target / ".clang-format")
        filter_cmake_presets(target, enabled_preset_groups)
        apply_preset_environments(target, preset_environments)

    print(f"[+] 已生成本机 Conan 模板目录：{generated_root}")
    return generated_root


def remove_existing_template_target(target_path, dest_root):
    resolved_dest = dest_root.resolve()
    absolute_target = target_path.absolute()
    if target_path.name not in TEMPLATE_NAMES:
        raise ValueError(f"拒绝删除非模板目录：{target_path}")
    if resolved_dest not in absolute_target.parents and absolute_target != resolved_dest:
        raise ValueError(f"拒绝删除目标根目录之外的路径：{target_path}")

    # 检测是否为符号链接或关接（Junction）
    is_link = target_path.is_symlink() or os.path.islink(target_path)
    if hasattr(target_path, "is_junction") and target_path.is_junction():
        is_link = True

    if is_link:
        try:
            target_path.unlink()
            return
        except Exception:
            pass
        # 在 Windows 上，目录联接（Junction）或目录符号链接可能需要用 rmdir 来删除
        try:
            target_path.rmdir()
            return
        except Exception:
            pass

    if target_path.exists():
        # 再次尝试用 rmdir（如果是未被上面检测到的 Junction/符号链接）
        try:
            target_path.rmdir()
            return
        except OSError:
            # 确为普通非空目录时才调用 rmtree
            shutil.rmtree(target_path)


def install_clangd_format_scripts(source_root):
    local_bin = Path("~/.local/bin").expanduser().resolve()
    local_bin.mkdir(parents=True, exist_ok=True)
    clang_format_path = source_root / ".clang-format"

    if not clang_format_path.exists():
        print("[!] 未找到根目录 .clang-format，跳过脚本生成。")
        return

    script_names = ["clangd_format", "clangd_foramt"]
    current_os = platform.system().lower()
    is_windows = current_os == "windows"

    for name in script_names:
        # Bash version
        bash_script = local_bin / name
        bash_content = (
            f"#!/bin/sh\n"
            f'cp -f "{clang_format_path.as_posix()}" .\n'
            f"if [ $? -eq 0 ]; then\n"
            f'    echo "[完成] 已成功复制 .clang-format 到当前目录。"\n'
            f"else\n"
            f'    echo "[错误] 复制 .clang-format 失败。"\n'
            f"fi\n"
        )
        try:
            bash_script.write_text(bash_content, encoding="utf-8")
            if not is_windows:
                bash_script.chmod(0o755)
            print(f"[+] 已写入 Bash 脚本：{bash_script}")
        except Exception as e:
            print(f"[!] 写入 Bash 脚本 {bash_script} 失败: {e}")

        # Batch version
        bat_script = local_bin / f"{name}.bat"
        bat_content = (
            f"@echo off\n"
            f'copy /Y "{clang_format_path}" . >nul\n'
            f"if %errorlevel% equ 0 (\n"
            f"    echo [完成] 已成功复制 .clang-format 到当前目录。\n"
            f") else (\n"
            f"    echo [错误] 复制 .clang-format 失败。\n"
            f")\n"
        )
        try:
            bat_script.write_text(bat_content, encoding="utf-8")
            print(f"[+] 已写入 CMD 脚本：{bat_script}")
        except Exception as e:
            print(f"[!] 写入 CMD 脚本 {bat_script} 失败: {e}")

        # PowerShell version
        ps1_script = local_bin / f"{name}.ps1"
        ps1_content = (
            f'Copy-Item -Path "{clang_format_path}" -Destination . -Force\n'
            f"if ($?) {{\n"
            f'    Write-Host "[完成] 已成功复制 .clang-format 到当前目录。" -ForegroundColor Green\n'
            f"}} else {{\n"
            f'    Write-Host "[错误] 复制 .clang-format 失败。" -ForegroundColor Red\n'
            f"}}\n"
        )
        try:
            ps1_script.write_text(ps1_content, encoding="utf-8")
            print(f"[+] 已写入 PowerShell 脚本：{ps1_script}")
        except Exception as e:
            print(f"[!] 写入 PowerShell 脚本 {ps1_script} 失败: {e}")


def setup_templates(source_root=None, dest_root=None, link=False, enabled_preset_groups=None, preset_environments=None):
    source_root = Path(source_root or Path(__file__).parent).resolve()
    dest_root = Path(dest_root or CONAN_HOME / "templates" / "command" / "new").expanduser().resolve()
    deploy_source_root = prepare_generated_templates(source_root, enabled_preset_groups, preset_environments)

    current_os = platform.system().lower()
    is_windows = current_os == "windows"

    print(f"[*] 检测到操作系统：{platform.system()}")
    print(f"[*] 源目录：{source_root}")
    print(f"[*] 部署模板目录：{deploy_source_root}")
    print(f"[*] 目标目录：{dest_root}")

    # 1. 同步根目录下的 .clang-format 到各个模板目录和已部署目录
    root_clang_format = source_root / ".clang-format"
    if root_clang_format.exists():
        for item_name in TEMPLATE_NAMES:
            template_dir = source_root / item_name
            if template_dir.exists() and template_dir.is_dir():
                shutil.copy2(root_clang_format, template_dir / ".clang-format")
                print(f"[+] 同步 .clang-format 到源模板目录：{item_name}")

            target_template_dir = dest_root / item_name
            if target_template_dir.exists() and target_template_dir.is_dir():
                try:
                    shutil.copy2(root_clang_format, target_template_dir / ".clang-format")
                    print(f"[+] 同步 .clang-format 到已部署模板目录：{item_name}")
                except Exception as e:
                    print(f"[!] 无法同步 .clang-format 到已部署模板目录 {target_template_dir}: {e}")

    # 2. 如果目标根目录不存在，自动创建
    if not dest_root.exists():
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
            print(f"[+] 创建目标根目录成功：{dest_root}")
        except Exception as e:
            print(f"[!] 无法创建目标目录：{e}")
            sys.exit(1)

    count = 0
    for item in iter_template_dirs(deploy_source_root):
        target_path = dest_root / item.name

        if target_path.exists() or target_path.is_symlink():
            try:
                remove_existing_template_target(target_path, dest_root)
                print(f"[+] 已替换旧模板入口：{item.name}")
            except Exception as e:
                print(f"[!] 无法替换旧模板入口 {item.name}: {e}")
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
                    print(f"[!] Windows Junction 创建失败 {item.name}: {result.stderr.strip()}")

            else:
                os.symlink(item, target_path)
                print(f"[+] Symlink 创建成功：{item.name}")
                count += 1

        except Exception as e:
            print(f"[!] 发生错误 {item.name}: {e}")

    action = "链接" if link else "复制"
    print(f"\n[*] 全部完成，共{action}了 {count} 个模板目录。")

    # 3. 自动生成并安装 clangd_format 脚本到 ~/.local/bin
    install_clangd_format_scripts(source_root)

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
        default=CONAN_HOME / "templates" / "command" / "new",
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
