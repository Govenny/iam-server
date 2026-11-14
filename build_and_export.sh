#!/usr/bin/env bash
set -e

IMAGE_NAME="iam-service"
IMAGE_TAG="latest"
OUTPUT_FILE="${IMAGE_NAME}.tar.gz"

echo ">>> [1/3] 构建 Python 3.12 镜像: ${IMAGE_NAME}:${IMAGE_TAG} ..."
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .

echo ">>> [2/3] 导出并压缩镜像至 ${OUTPUT_FILE} ..."
docker save ${IMAGE_NAME}:${IMAGE_TAG} | gzip >${OUTPUT_FILE}

echo ">>> [3/3] 打包完成！"
ls -lh ${OUTPUT_FILE}
echo "服务器加载并启动命令: docker load -i ${OUTPUT_FILE} && docker compose up -d iam-service"
