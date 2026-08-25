# Conan C++ 项目模板

这是一组基于 Conan 2、CMake 和 Ninja 的 C++ 项目模板。依赖版本由 `uv.lock` 固定；安装时既创建仓库内的 `.venv`，也使用 `uv tool` 安装用户级全局命令。换电脑后可以恢复相同版本，并修复指向旧 Python 路径的全局启动器。

## 新电脑快速安装

先克隆或下载本仓库，然后在仓库根目录执行：

### Windows（PowerShell）

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

### macOS / Linux

```sh
sh ./bootstrap.sh
```

两个启动器最终都会进入同一个 `setup.py`。脚本会自动识别 Windows、macOS 或 Linux；Linux 还会依次识别 `apt-get`、`dnf`、`pacman`、`zypper`，macOS 使用已有的 Homebrew，Windows 使用 WinGet 或项目内工具链目录。

启动脚本会：

1. 在缺少 uv 时安装仓库指定的 uv 版本；
2. 按 `uv.lock` 创建 `.venv`；
3. 从锁文件读取版本，通过 `uv tool` 全局安装 Conan、CMake、Ninja 和 Ruff；
4. 交互选择并检查或安装 C/C++ 编译器、C++ 标准库和链接器；
5. 把所选标准库和链接器写入 Conan profile，并只注册匹配的 CMake presets。

Python 默认由 `.python-version` 固定为 3.12（脚本兼容 3.12–3.14）；如果本机没有该版本，uv 会自动准备隔离的 Python。交互终端会显示当前平台有效的选项，不兼容组合不会出现在菜单中：

| 系统 | 编译器 | C++ 标准库 | 链接器 |
| --- | --- | --- | --- |
| Windows | Clang、GCC、MSVC | libc++、libstdc++（GCC）、Microsoft STL | lld、GNU ld.bfd、link.exe |
| macOS | Clang、GCC | libc++（Clang）、libstdc++（GCC） | 系统链接器、lld |
| Linux | Clang、GCC | Clang 可选 libc++/libstdc++，GCC 使用 libstdc++ | 系统链接器、ld.bfd、lld、mold |

Windows 上选择 `Clang + Microsoft STL` 时使用 clang-cl/MSVC ABI；选择 `Clang + libc++` 时使用 llvm-mingw。MSVC 不支持 libc++ 或 libstdc++，GCC 固定使用 libstdc++。

## 常用模式

普通终端运行 bootstrap 时会显示交互菜单。也可以显式指定组合，适合自动化或重复安装：

```powershell
.\bootstrap.ps1 -Compiler clang -Stdlib libc++ -Linker lld -NonInteractive
```

```sh
sh ./bootstrap.sh --compiler clang --stdlib libstdc++ --linker mold --non-interactive
```

参数可选值：

- `--compiler`: `auto`、`clang`、`gcc`、`msvc`
- `--stdlib`: `auto`、`libc++`、`libstdc++`、`msvc`
- `--linker`: `auto`、`system`、`lld`、`bfd`、`mold`、`msvc`

`auto` 在交互终端中表示显示菜单；配合 `--non-interactive` 时，会优先复用本机已有工具链，否则采用系统默认组合。

只诊断项目环境、全局 uv tools 和宿主工具链，不执行安装或模板注册：

```powershell
.\bootstrap.ps1 -CheckOnly
```

```sh
sh ./bootstrap.sh --check-only
```

无交互诊断示例：

```sh
sh ./bootstrap.sh --check-only --compiler gcc --stdlib libstdc++ --linker bfd --non-interactive
```

Windows 无法创建链接时，改为复制 Conan 模板：

```powershell
.\bootstrap.ps1 -CopyTemplates
```

macOS / Linux 同样可传递现有 `setup.py` 参数：

```sh
sh ./bootstrap.sh --copy-templates
```

验证锁文件与脚本：

```sh
uv lock --check
uv run --locked python -m unittest discover -s tests -v
uv run --locked python setup.py --check-only
```

## 日常使用

安装完成后，Conan、CMake、Ninja 和 Ruff 都可以在任意目录直接调用：

```sh
conan --version
cmake --version
ninja --version
ruff --version
```

创建项目：

```sh
conan new basic_exe -d name=my_app -d version=0.1.0
```

如果当前终端尚未刷新 PATH，可以重新打开终端，或暂时通过项目环境执行：

```sh
uv run --locked conan --version
uv run --locked conan new basic_exe -d name=my_app -d version=0.1.0
```

也可以先激活 `.venv`，再正常使用 `conan`、`cmake` 与 `ninja`。

## 维护依赖

日常安装始终使用锁文件，不会因为上游发布新版本而自动漂移：

```sh
uv sync --locked
```

需要主动升级时才更新锁文件，并在更新后重新运行测试和环境检查：

```sh
uv lock --upgrade
uv sync --locked
```

`.venv`、`.generated` 和 `logs` 都是可再生成内容，不需要迁移到新电脑。

如需隔离多套 Conan 配置，可在运行前设置 `CONAN_HOME`；安装脚本、profile 生成和模板注册都会使用该目录。

如果只需要重新安装锁定版本的用户级全局工具，可以运行：

```sh
uv run --locked python install_uv_tools.py
```
