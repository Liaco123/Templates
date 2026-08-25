#!/usr/bin/env sh
set -eu

UV_VERSION="0.11.28"
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_ROOT"

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return
    fi
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    return 1
}

UV_BIN=$(find_uv || true)
if [ -z "$UV_BIN" ]; then
    printf '[信息] 未检测到 uv，安装固定版本 %s。\n' "$UV_VERSION"
    export UV_NO_MODIFY_PATH=1
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
    else
        printf '%s\n' '[错误] 需要 curl 或 wget 才能自动安装 uv。' >&2
        exit 1
    fi
    UV_BIN=$(find_uv || true)
fi

if [ -z "$UV_BIN" ]; then
    printf '%s\n' '[错误] uv 安装后仍不可用，请按官方文档手动安装。' >&2
    exit 1
fi

printf '%s\n' '[阶段] 同步项目内 Python 工具链'
"$UV_BIN" sync --locked

printf '%s\n' '[阶段] 初始化开发环境与 Conan 模板'
exec "$UV_BIN" run --locked python setup.py "$@"
