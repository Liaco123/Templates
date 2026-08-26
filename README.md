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
4. 交互选择项目默认的编译器、C++ 标准库和链接器，再逐项选择是否安装或升级 GCC、LLVM 和 MSVC；
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

### 编译器安装、版本选择与升级

交互运行时，脚本会显示每项工具链的已安装版本，并分别询问：

1. 是否安装或管理 GCC；
2. 是否安装或管理 LLVM；Windows 上还会区分 llvm-mingw（clang++/libc++）与 clang-cl/MSVC；
3. Windows 上是否安装或管理 MSVC Build Tools；
4. 已安装工具链是否升级或切换版本。

未确认的现有工具链不会升级。Windows 会动态读取稳定发行版列表：GCC 使用 WinLibs GitHub Releases，LLVM 使用 llvm-project 或 llvm-mingw GitHub Releases，MSVC 优先使用 WinGet 中可用的 Visual Studio Build Tools 版本；没有 WinGet 时回退到 Microsoft 官方的 Visual Studio 2026/2022 evergreen bootstrapper。检测到已登录的 `gh` 时优先通过 `gh api` 读取 GitHub 发行版，避免匿名 API 限额；否则使用匿名 HTTPS。GitHub 资产校验发布方提供的 SHA-256，Microsoft bootstrapper 校验 Authenticode 发布者签名；归档工具链安装到版本目录，例如 `C:\dev\gcc\16.2.0`，不会覆盖其他版本。

Ubuntu/Debian（包括 WSL）使用 APT 时也会列出具体版本：GCC 只显示仓库中同时存在 `gcc-N` 和 `g++-N` 的版本；LLVM 会合并当前 APT 仓库版本与 [apt.llvm.org](https://apt.llvm.org/) 针对当前发行版提供的稳定、资格和开发分支。选择 apt.llvm.org 版本后，脚本会验证官方软件源密钥指纹，再配置带 `signed-by` 限定的版本化软件源，并安装匹配的 `clang-N`；选择 lld 或 libc++ 时会同步安装相同主版本的 `lld-N`、`libc++-N-dev` 和 `libc++abi-N-dev`。其他 Linux 包管理器和 macOS 仍显示其系统仓库候选版本。

例如，在 Ubuntu/WSL 上明确升级到 LLVM 22，并让 Clang 22 配合 GNU libstdc++ 与 lld 22：

```sh
sh ./bootstrap.sh \
  --compiler clang \
  --stdlib libstdc++ \
  --linker lld \
  --install-toolchain llvm \
  --toolchain-version llvm=22 \
  --upgrade-toolchains \
  --non-interactive
```

无交互安装或升级多个工具链：

```powershell
.\bootstrap.ps1 `
  -InstallToolchain gcc,llvm `
  -ToolchainVersion "gcc=16.2.0","llvm=22.1.8" `
  -UpgradeToolchains `
  -LlvmVariant mingw `
  -NonInteractive
```

```sh
sh ./bootstrap.sh \
  --install-toolchain gcc \
  --install-toolchain llvm \
  --toolchain-version gcc=16.2.0 \
  --toolchain-version llvm=22.1.8 \
  --upgrade-toolchains \
  --non-interactive
```

相关参数：

- `--install-toolchain`: `gcc`、`llvm`、`msvc`，可重复指定；
- `--toolchain-version NAME=VERSION`: 为已选择的工具链指定 `latest`、主版本或完整版本；
- `--upgrade-toolchains`: 允许修改已有安装；省略 `--install-toolchain` 时升级主工具链所必需的编译器组件；
- `--llvm-variant`: Windows 可选 `auto`、`mingw`、`msvc`。

只指定安装目标但不加 `--upgrade-toolchains` 时，已安装工具链保持不变；未安装工具链仍会安装。MSVC 只在 Windows 上可选。

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
