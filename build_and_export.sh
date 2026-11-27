#!/usr/bin/env bash
set -e

IMAGE_NAME="iam-service"
IMAGE_TAG="latest"
PLATFORM="${TARGET_PLATFORM:-${1:-linux/arm64}}"
OUTPUT_FILE="${IMAGE_NAME}.tar.gz"

echo "=========================================================="
echo ">>> [1/3] 构建 Python 3.12 镜像 (架构: ${PLATFORM}): ${IMAGE_NAME}:${IMAGE_TAG} ..."
echo "=========================================================="

export DOCKER_BUILDKIT=1

# 默认针对 arm64 架构构建，优先复用支持 BuildKit 缓存且兼容中文目录的本地 builder
if docker buildx version >/dev/null 2>&1; then
    if docker buildx inspect desktop-linux >/dev/null 2>&1; then
        docker buildx build --builder desktop-linux --platform "${PLATFORM}" -t "${IMAGE_NAME}:${IMAGE_TAG}" --load . || \
        docker build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${IMAGE_TAG}" .
    else
        docker buildx build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${IMAGE_TAG}" --load . || \
        docker build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${IMAGE_TAG}" .
    fi
else
    docker build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${IMAGE_TAG}" .
fi

echo ""
echo "=========================================================="
echo ">>> [2/3] 导出并压缩镜像至 ${OUTPUT_FILE} ..."
echo "=========================================================="
docker save "${IMAGE_NAME}:${IMAGE_TAG}" | gzip >"${OUTPUT_FILE}"

echo ""
echo "=========================================================="
echo ">>> [3/3] 打包完成！"
echo "=========================================================="
ls -lh "${OUTPUT_FILE}"
echo ""
echo "已成功生成目标架构 [${PLATFORM}] 镜像压缩包: ${OUTPUT_FILE}"
echo "服务器 (ARM64) 导入与启动命令:"
echo "  docker load -i ${OUTPUT_FILE}"
echo "  docker compose up -d iam-service"
