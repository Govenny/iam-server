FROM python:3.12-slim

WORKDIR /app

ARG TARGETARCH

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN --mount=type=bind,source=wheels,target=/tmp/wheels-all,readonly \
    --mount=type=cache,id=pip-cache-${TARGETARCH},target=/root/.cache/pip \
    if ls /tmp/wheels-all/${TARGETARCH}/*.whl >/dev/null 2>&1; then \
        echo ">>> 命中本地离线依赖包 (/tmp/wheels-all/${TARGETARCH})，离线极速安装中 ..." && \
        (pip install --no-cache-dir --disable-pip-version-check --no-index --no-deps /tmp/wheels-all/${TARGETARCH}/*.whl || \
         pip install --no-cache-dir --disable-pip-version-check --no-index --find-links="/tmp/wheels-all/${TARGETARCH}" -r requirements.txt || \
         pip install --prefer-binary --find-links="/tmp/wheels-all/${TARGETARCH}" --default-timeout=60 --retries=3 \
             -i https://mirrors.aliyun.com/pypi/simple/ \
             --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
             -r requirements.txt); \
    else \
        echo ">>> 未检测到本地离线包，使用在线镜像下载并缓存 ..." && \
        pip install --prefer-binary --default-timeout=60 --retries=3 \
            -i https://mirrors.aliyun.com/pypi/simple/ \
            --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
            -r requirements.txt; \
    fi

COPY ./app /app/app
COPY ./sql /app/sql
COPY ./web_dist /app/web_dist
COPY ./scripts /app/scripts

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
