#!/usr/bin/env bash
set -e

ARCH="${1:-arm64}"
MIRROR="${PIP_MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"
FALLBACK_MIRROR="${PIP_FALLBACK_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

download_arch() {
    local target="$1"
    local out_dir="${SCRIPT_DIR}/wheels/${target}"
    mkdir -p "${out_dir}"

    local plat_flags=()
    if [ "${target}" = "arm64" ]; then
        plat_flags=(
            --platform manylinux_2_34_aarch64
            --platform manylinux_2_28_aarch64
            --platform manylinux_2_17_aarch64
            --platform manylinux2014_aarch64
            --platform linux_aarch64
        )
    elif [ "${target}" = "amd64" ]; then
        plat_flags=(
            --platform manylinux_2_34_x86_64
            --platform manylinux_2_28_x86_64
            --platform manylinux_2_17_x86_64
            --platform manylinux2014_x86_64
            --platform linux_x86_64
        )
    else
        echo "Unsupported arch: ${target} (use arm64, amd64, or all)"
        exit 1
    fi

    echo "=========================================================="
    echo ">>> 下载 Python 3.12 (linux/${target}) 离线包 -> ${out_dir}"
    echo "=========================================================="

    python3 -m pip download \
        -r "${SCRIPT_DIR}/requirements.txt" \
        "uvloop>=0.15.1" \
        -d "${out_dir}" \
        "${plat_flags[@]}" \
        --python-version 3.12 \
        --implementation cp \
        --abi cp312 \
        --only-binary=:all: \
        -i "${MIRROR}" \
        --extra-index-url "${FALLBACK_MIRROR}"

    local count
    count=$(find "${out_dir}" -maxdepth 1 -name '*.whl' | wc -l)
    echo ">>> [完成] linux/${target} 已就绪 ${count} 个 .whl 包"
}

if [ "${ARCH}" = "all" ]; then
    download_arch arm64
    download_arch amd64
else
    download_arch "${ARCH}"
fi

echo ""
echo "现在可直接运行 ./build_and_export.sh，Docker 将自动使用本地离线包极速安装！"
