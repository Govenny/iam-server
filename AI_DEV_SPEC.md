# 数智底座用户与角色权限中心 — 伴生服务（SDK 后端 + 嵌入前端）研发规范

## 1. 架构定位与研发红线（Antigravity 必读）

1. **零侵入 Casdoor 底座**：严禁修改、编译 Casdoor 源码。官方 `casdoor` 容器（端口 `8000`）仅作为底层认证引擎与基础用户/角色存储运行。
2. **技术栈强约束**：
   * 后端服务（`iam-service`，端口 `8001`）：**Python 3.12** + FastAPI + SQLAlchemy 2.0 + PyMySQL + Redis + 官方 `casdoor` Python SDK。
   * 前端页面：Vue 3（或单页 HTML/JS）打包后直接挂载于 `iam-service` 的 `/ui/*` 路由下，供门户通过 `<iframe>` 嵌入展示，不额外申请新端口。
3. **同库共存策略**：
   * 直接连接现有 MySQL 实例中的 **`casdoor` 数据库**（不新建库）。
   * 复用 Casdoor 原生 `user`、`organization`、`role`、`casbin_rule` 表；服务启动时自动幂等执行建表 SQL，创建 `uc_*` 和 `perm_*` 扩展表。
4. **旁路增强与无缝衔接策略（严禁推翻门户现有功能）**：
   * **现有门户现状**：已具备“个人注册/登录 $\rightarrow$ 企业认证提报 $\rightarrow$ 后台企业审核”完整链路，角色仅区分管理员/非管理员，且已与部分旧子平台打通 SSO。**严禁重做登录页、注册页和企业审核页**，避免破坏现有演示环境。
   * **本服务职责边界**：
     1. 门户的“个人游客”映射为本系统的 **`PERSONAL` 租户 + `MASTER` 主账号**；门户的“企业账号”映射为 **`ENTERPRISE` 租户（`cert_level=2`）+ `MASTER` 主账号**。
     2. 本服务通过**“门户状态同步接口 + JIT 懒加载兜底”**自动与门户对齐用户与企业状态，补齐门户缺失的**多租户工作空间（Workspace）、企业主子账号树、7 大标准角色权限分配、跨平台 60s 免密 Jump Ticket 及网关 Header 签名**。
     3. 企业主账号在本服务页面创建的“子账号”（仅存在于 Casdoor 中），通过本服务提供的 `sub-login` 接口接入门户登录兜底验证，实现无缝登录。
5. **协同分工与测试方式**：
   * **全职开发**：负责 Docker 编排、数据库建表、FastAPI 全部后端接口及 CLI 自动化测试脚本。
   * **兼职开发**：负责 `8001/ui/*` 下的 3 个嵌入页面（成员与角色管理、工作空间切换/管理、API Key 管理）。
   * **纯命令行与 Swagger 测试**：不单独搭建 `9000/9001` 测试网页，直接通过 FastAPI `/docs` 和 `scripts/test_cli.py` 脚本对 `8000` 与 `8001` 发起全链路自动化测试。

---

## 2. 工程目录结构

```text
shuzidizuo-iam/
├── 用户与权限中心详细设计说明书.md    # 详设规范参考基线
├── AI_DEV_SPEC.md                     # 本研发执行规范
├── docker-compose.yml                 # 统一容器编排（casdoor + iam-service + db + redis）
├── Dockerfile                         # Python 3.12 生产镜像构建文件
├── build_and_export.sh                # Linux/Mac 一键构建并导出 .tar.gz 脚本
├── build_and_export.ps1               # Windows PowerShell 一键构建并导出脚本
├── requirements.txt                   # Python 3.12 依赖清单
├── .env.example                       # 环境变量模板
├── sql/
│   ├── 01_phase1_core.sql             # 第一目标：5 张核心扩展表 DDL
│   └── 02_phase2_ext.sql              # 第二目标：2 张业务扩展表 DDL
├── app/
│   ├── main.py                        # FastAPI 入口（自动建表、挂载 /ui 静态前端、CORS）
│   ├── core/
│   │   ├── config.py                  # 配置管理（Pydantic Settings）
│   │   ├── database.py                # SQLAlchemy MySQL 连接池
│   │   ├── redis_client.py            # Redis 连接池
│   │   ├── casdoor_client.py          # 封装官方 CasdoorSDK（建用户、验密码、解析 Token）
│   │   └── security.py                # AES-256 Ticket加解密、HMAC-SHA256网关签名、SM3/SHA256
│   ├── models/                        # ORM 模型定义
│   ├── schemas/                       # Pydantic 请求/响应模型
│   └── api/
│       └── v1/
│           ├── portal_sync.py         # 门户用户/企业同步、JIT懒加载、iframe换票、子账号登录兜底
│           ├── sub_account.py         # 企业主子账号树创建与查询（物化路径）
│           ├── workspace_perm.py      # Workspace 管理、7大标准角色与模块准入查询
│           ├── jump_ticket.py         # 跨平台 60s 免密 Jump Ticket 签发与核销 + 网关签名生成
│           ├── api_key.py             # [第二目标] API Key 生成、双Key平滑轮换与网关验权
│           └── shadow_grant.py        # [第二目标] 可信流通交易契约回调与影子账号路由
├── web_dist/                          # 前端打包产物目录（挂载到 8001/ui/*）
└── scripts/
    └── test_cli.py                    # 纯命令行全链路自动化测试脚本
```

---

## 3. 容器编排与一键打包脚本

### 3.1 `docker-compose.yml`（保持原有服务不变，新增 `iam-service`）

```yaml
services:
  casdoor:
    restart: always
    image: casbin/casdoor:latest
    entrypoint: /bin/sh -c './server --createDatabase=true'
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      RUNNING_IN_DOCKER: "true"
    volumes:
      - ./conf:/conf/
    networks:
      - casdoor-net

  iam-service:
    restart: always
    image: iam-service:latest
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8001:8001"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      casdoor:
        condition: service_started
    environment:
      APP_ENV: "prod"
      APP_PORT: "8001"
      MYSQL_DSN: "mysql+pymysql://root:123456@db:3306/casdoor?charset=utf8mb4"
      REDIS_URL: "redis://redis:6379/0"
      # 容器内网调用 Casdoor 地址
      CASDOOR_INTERNAL_ENDPOINT: "http://casdoor:8000"
      CASDOOR_CLIENT_ID: "${CASDOOR_CLIENT_ID:-your_client_id}"
      CASDOOR_CLIENT_SECRET: "${CASDOOR_CLIENT_SECRET:-your_client_secret}"
      CASDOOR_ORG_NAME: "${CASDOOR_ORG_NAME:-built-in}"
      CASDOOR_APP_NAME: "${CASDOOR_APP_NAME:-app-built-in}"
      # 现有门户用户信息反查接口（用于 iframe 打开时验证 portal_token，留空则启用本地Mock解析）
      PORTAL_USERINFO_URL: "${PORTAL_USERINFO_URL:-}"
      # 安全密钥（生产部署时通过 .env 覆盖）
      JUMP_TICKET_AES_KEY: "${JUMP_TICKET_AES_KEY:-0123456789abcdef0123456789abcdef}"
      GATEWAY_HMAC_SECRET: "${GATEWAY_HMAC_SECRET:-shuzidizuo_gw_secret_2026}"
      INTERNAL_JWT_SECRET: "${INTERNAL_JWT_SECRET:-shuzidizuo_iam_jwt_secret_2026}"
    volumes:
      - ./sql:/app/sql
      - ./web_dist:/app/web_dist
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health')\""]
      interval: 15s
      timeout: 5s
      retries: 3
    networks:
      - casdoor-net

  db:
    restart: always
    image: mysql:8.0
    ports:
      - "3306:3306"
    environment:
      MYSQL_ROOT_PASSWORD: 123456
    volumes:
      - /data/mysql:/var/lib/mysql
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1 -uroot -p$$MYSQL_ROOT_PASSWORD --silent"]
      interval: 10s
      timeout: 5s
      start_period: 60s
      retries: 5
    networks:
      - casdoor-net

  redis:
    restart: always
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - /data/redis:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - casdoor-net

networks:
  casdoor-net:
    driver: bridge
```

### 3.2 `Dockerfile`（Python 3.12 镜像）

```dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY ./app /app/app
COPY ./sql /app/sql
COPY ./web_dist /app/web_dist
COPY ./scripts /app/scripts

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
```

### 3.3 `requirements.txt`（兼容 Python 3.12）

```text
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sqlalchemy>=2.0.35
pymysql>=1.1.1
cryptography>=43.0.0
redis>=5.0.8
pydantic>=2.9.0
pydantic-settings>=2.5.0
casdoor>=1.18.0
httpx>=0.27.2
python-multipart>=0.0.9
PyJWT>=2.9.0
gmssl>=3.2.2
```

### 3.4 一键构建与导出镜像脚本

**Linux / Mac (`build_and_export.sh`)**：

```bash
#!/usr/bin/env bash
set -e

IMAGE_NAME="iam-service"
IMAGE_TAG="latest"
PLATFORM="${TARGET_PLATFORM:-${1:-linux/arm64}}"
OUTPUT_FILE="${IMAGE_NAME}.tar.gz"

echo ">>> [1/3] 构建 Python 3.12 镜像 (默认架构: ${PLATFORM}): ${IMAGE_NAME}:${IMAGE_TAG} ..."
if docker buildx version >/dev/null 2>&1; then
    docker buildx build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${IMAGE_TAG}" --load . || \
    docker build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${IMAGE_TAG}" .
else
    docker build --platform "${PLATFORM}" -t "${IMAGE_NAME}:${IMAGE_TAG}" .
fi

echo ">>> [2/3] 导出并压缩镜像至 ${OUTPUT_FILE} ..."
docker save ${IMAGE_NAME}:${IMAGE_TAG} | gzip >${OUTPUT_FILE}

echo ">>> [3/3] 打包完成！"
ls -lh ${OUTPUT_FILE}
echo "服务器加载并启动命令: docker load -i ${OUTPUT_FILE} && docker compose up -d iam-service"
```

**Windows PowerShell (`build_and_export.ps1`)**：

```powershell
$ErrorActionPreference = "Stop"
$ImageName = "iam-service"
$ImageTag = "latest"
$OutputFile = "$ImageName.tar.gz"

Write-Host ">>> [1/3] 构建 Python 3.12 镜像: ${ImageName}:${ImageTag} ..." -ForegroundColor Cyan
docker build -t "${ImageName}:${ImageTag}" .

Write-Host ">>> [2/3] 导出并压缩镜像至 $OutputFile ..." -ForegroundColor Cyan
docker save "${ImageName}:${ImageTag}" | gzip > $OutputFile

Write-Host ">>> [3/3] 打包完成: $(Resolve-Path $OutputFile)" -ForegroundColor Green
```

---

## 4. 数据库物理表设计（直接建在现有 `casdoor` 库中）

不新建数据库，直接连接 `casdoor` 库，复用原生 `user`、`organization`、`role`、`casbin_rule` 表，补充以下扩展表：

### 4.1 第一目标核心扩展表（`sql/01_phase1_core.sql`）

```sql
USE casdoor;

-- 1. 统一租户与实名主体表（与门户“个人游客/企业账号”状态同步）
CREATE TABLE IF NOT EXISTS `uc_tenant` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增物理主键',
  `tenant_id` varchar(64) NOT NULL COMMENT '全局租户唯一标识，如 ten_88880001',
  `tenant_type` varchar(32) NOT NULL DEFAULT 'PERSONAL' COMMENT '租户类型: PERSONAL(个人单人租户)/ENTERPRISE(企业租户)',
  `org_name` varchar(128) NOT NULL COMMENT '对应 Casdoor 原生 organization.name',
  `tenant_code` varchar(64) NOT NULL COMMENT '租户英文代号',
  `tenant_name` varchar(255) NOT NULL COMMENT '企业全称或个人租户名称',
  `cert_level` tinyint(1) NOT NULL DEFAULT '0' COMMENT '实名等级: 0-未实名(个人游客), 1-个人实名, 2-企业实名(企业账号)',
  `credit_code` varchar(18) DEFAULT NULL COMMENT '统一社会信用代码',
  `legal_person_name` varchar(64) DEFAULT NULL COMMENT '法人/个人姓名',
  `cert_status` tinyint(1) NOT NULL DEFAULT '0' COMMENT '实名状态: 0-未认证, 1-审核中, 2-已通过, 3-已驳回, 4-已过期, 5-已吊销',
  `trusted_data_synced` tinyint(1) NOT NULL DEFAULT '0' COMMENT '可信流通同步标记: 0-未同步, 1-已同步',
  `is_exempt_billing` tinyint(1) NOT NULL DEFAULT '0' COMMENT '免计费白名单: 0-正常计费, 1-免扣费(官方演示租户)',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  `is_deleted` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tenant_id` (`tenant_id`),
  UNIQUE KEY `uk_org_name` (`org_name`),
  UNIQUE KEY `uk_tenant_code` (`tenant_code`),
  KEY `idx_tenant_type` (`tenant_type`),
  KEY `idx_cert_level` (`cert_level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='统一租户与实名主体表';

-- 2. 统一用户与主子账号树扩展表（绑定门户 user_id 与 Casdoor user）
CREATE TABLE IF NOT EXISTS `uc_user_profile` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `user_id` varchar(64) NOT NULL COMMENT '全局用户ID，如 usr_9a8b7c6d',
  `portal_user_id` varchar(128) DEFAULT NULL COMMENT '存量门户系统用户ID（企业子账号此字段为空）',
  `casdoor_user_id` varchar(128) NOT NULL COMMENT 'Casdoor原生 user.id 或 owner/name',
  `tenant_id` varchar(64) NOT NULL COMMENT '所属租户ID',
  `account_type` varchar(32) NOT NULL DEFAULT 'MASTER' COMMENT '账号主子属性: MASTER(主账号)/SUB(企业子账号)',
  `parent_user_id` varchar(64) NOT NULL DEFAULT '' COMMENT '直接上级用户ID，主账号为空',
  `tree_path` varchar(512) NOT NULL COMMENT '物化路径，如 /usr_root_001/usr_sub_101/',
  `depth_level` tinyint unsigned NOT NULL DEFAULT '1' COMMENT '层级深度: 1-主账号, 2-二级子账号, 3-三级子账号',
  `quota_share_enabled` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否共享主账号配额: 1-共享, 0-独立受限',
  `status` tinyint(1) NOT NULL DEFAULT '1' COMMENT '账号状态: 1-正常, 2-冻结, 3-注销',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  `is_deleted` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_id` (`user_id`),
  UNIQUE KEY `uk_casdoor_uid` (`casdoor_user_id`),
  KEY `idx_portal_uid` (`portal_user_id`),
  KEY `idx_tenant_type` (`tenant_id`,`account_type`),
  KEY `idx_tree_path` (`tree_path`(128))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='统一用户与主子账号树扩展表';

-- 3. 租户工作空间与底层底座映射表
CREATE TABLE IF NOT EXISTS `perm_tenant_workspace` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `workspace_id` varchar(64) NOT NULL COMMENT '工作空间全局唯一标识，如 ws_default_001',
  `tenant_id` varchar(64) NOT NULL COMMENT '所属租户ID',
  `workspace_name` varchar(128) NOT NULL COMMENT '工作空间显示名称',
  `is_default` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否默认主空间: 1-是, 0-否',
  `k8s_namespace` varchar(64) NOT NULL DEFAULT '' COMMENT '映射算力平台K8s Namespace',
  `model_project_id` varchar(64) NOT NULL DEFAULT '' COMMENT '映射模型工程平台项目ID',
  `agent_space_id` varchar(64) NOT NULL DEFAULT '' COMMENT '映射Agent工程平台沙箱ID',
  `caict_workspace_id` varchar(64) NOT NULL DEFAULT '' COMMENT '映射信通院数据工程空间ID',
  `quota_config_json` json NOT NULL COMMENT '空间物理配额配置JSON',
  `status` tinyint(1) NOT NULL DEFAULT '1' COMMENT '状态: 1-活跃, 2-冻结, 3-释放',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_workspace_id` (`workspace_id`),
  KEY `idx_tenant_id` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='租户工作空间与底层底座映射表';

-- 4. 用户-角色-工作空间绑定关联表（7大标准角色 + 平台模块准入开关）
CREATE TABLE IF NOT EXISTS `perm_user_role_workspace` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `tenant_id` varchar(64) NOT NULL COMMENT '所属租户ID',
  `user_id` varchar(64) NOT NULL COMMENT '被授权用户ID',
  `workspace_id` varchar(64) NOT NULL COMMENT '绑定的工作空间ID',
  `role_code` varchar(64) NOT NULL COMMENT '7大标准角色编码: PLATFORM_ADMIN/PLATFORM_OPERATOR/PLATFORM_DEMO/TENANT_ADMIN/TENANT_DEVELOPER/TENANT_FINANCE/TENANT_VIEWER',
  `allowed_modules` json NOT NULL COMMENT '平台大模块准入白名单数组，如 ["platform:model_engine:module:*"]',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_ws_role` (`user_id`,`workspace_id`,`role_code`),
  KEY `idx_tenant_ws` (`tenant_id`,`workspace_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='用户-角色-空间绑定与模块裁剪表';

-- 5. 跨平台异构影子账号路由映射表（预留对接信通院等存量平台）
CREATE TABLE IF NOT EXISTS `uc_shadow_account_mapping` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `mapping_id` varchar(64) NOT NULL COMMENT '映射唯一编码',
  `tenant_id` varchar(64) NOT NULL COMMENT '门户全局租户ID',
  `user_id` varchar(64) NOT NULL COMMENT '门户全局用户ID',
  `target_platform_code` varchar(32) NOT NULL COMMENT '目标异构平台编码，如 CAICT_DATA_ENG',
  `shadow_tenant_id` varchar(128) NOT NULL DEFAULT '' COMMENT '异构系统内部租户ID',
  `shadow_user_id` varchar(128) NOT NULL COMMENT '异构系统内部原生账号ID',
  `shadow_account_name` varchar(128) NOT NULL DEFAULT '' COMMENT '异构系统内部登录名',
  `mapping_type` tinyint(1) NOT NULL DEFAULT '1' COMMENT '绑定方式: 1-JIT静默自动创号, 2-人工手工绑定',
  `sync_status` tinyint(1) NOT NULL DEFAULT '1' COMMENT '状态: 1-正常, 2-同步中, 3-失败',
  `ext_meta_json` json DEFAULT NULL COMMENT '扩展凭据元数据',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  `is_deleted` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_mapping_id` (`mapping_id`),
  UNIQUE KEY `uk_user_plat` (`user_id`,`target_platform_code`,`is_deleted`),
  KEY `idx_plat_shadow_uid` (`target_platform_code`,`shadow_user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='跨平台异构影子账号路由映射表';
```

### 4.2 第二目标扩展表（`sql/02_phase2_ext.sql`）

```sql
USE casdoor;

-- 6. 对外服务订阅凭证表（能力商店 API Key）
CREATE TABLE IF NOT EXISTS `perm_api_key` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `key_id` varchar(64) NOT NULL COMMENT '内部管理唯一标识',
  `tenant_id` varchar(64) NOT NULL COMMENT '所属租户ID',
  `created_by_uid` varchar(64) NOT NULL COMMENT '创建人user_id',
  `key_name` varchar(128) NOT NULL COMMENT '凭证助记名称',
  `access_key` varchar(64) NOT NULL COMMENT '外部公开调用Key，如 ak-prod-xxx',
  `secret_key_hash` char(64) NOT NULL COMMENT '私密密钥加盐摘要哈希(SM3/SHA256)',
  `salt` varchar(32) NOT NULL COMMENT '加盐随机串',
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态: ACTIVE/ROTATING/DISABLED/REVOKED',
  `allowed_scopes` json NOT NULL COMMENT '允许访问的服务URI白名单数组',
  `ip_whitelist` varchar(512) NOT NULL DEFAULT '' COMMENT '客户端IP白名单CIDR',
  `qps_limit` int unsigned NOT NULL DEFAULT '100' COMMENT '每秒调用限流上限',
  `daily_call_limit` bigint unsigned NOT NULL DEFAULT '100000' COMMENT '每日最大调用频次',
  `expire_at` datetime(3) NOT NULL COMMENT '凭证失效时间',
  `grace_period_end` datetime(3) DEFAULT NULL COMMENT '双Key轮换过渡期截止时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  `is_deleted` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_key_id` (`key_id`),
  UNIQUE KEY `uk_access_key` (`access_key`),
  KEY `idx_tenant_status` (`tenant_id`,`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='对外服务订阅凭证表';

-- 7. 服务订阅与数据交易授权关系表（可信流通契约回调）
CREATE TABLE IF NOT EXISTS `perm_service_subscription_grant` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `grant_id` varchar(64) NOT NULL COMMENT '授权业务流水号',
  `tenant_id` varchar(64) NOT NULL COMMENT '被授权买方租户ID',
  `source_type` varchar(32) NOT NULL COMMENT '来源: STORE_SUBSCRIPTION / TRUSTED_DATA_CONTRACT',
  `source_contract_id` varchar(64) NOT NULL COMMENT '关联订单号或可信流通契约编号',
  `resource_uri` varchar(255) NOT NULL COMMENT '被授权的目标资产URI',
  `granted_actions` varchar(64) NOT NULL DEFAULT 'READ' COMMENT '授权动作集合，如 READ,EXECUTE',
  `grant_status` varchar(32) NOT NULL DEFAULT 'EFFECTIVE' COMMENT '状态: EFFECTIVE/EXPIRED/TERMINATED',
  `valid_from` datetime(3) NOT NULL COMMENT '生效起始时间',
  `valid_to` datetime(3) NOT NULL COMMENT '授权终止时间',
  `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  `is_deleted` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_grant_id` (`grant_id`),
  KEY `idx_tenant_source` (`tenant_id`,`source_type`),
  KEY `idx_contract` (`source_contract_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='服务订阅与数据交易授权关系表';
```

---

## 5. 核心后端接口规范（`8001` FastAPI 服务）

### 5.1 门户存量衔接与懒加载兜底接口（`app/api/v1/portal_sync.py`）

1. **`POST /api/v1/sync/user`（门户注册/登录静默双写同步，幂等）**
* **入参**：`{"portal_user_id": "p_1001", "username": "zhangsan", "display_name": "张三", "phone": "13800138000", "is_platform_admin": false, "is_enterprise": false, "company_name": ""}`
* **核心逻辑**：
* 查 `uc_user_profile` 是否已存在该 `portal_user_id`。
* 若不存在（首次同步）：调用 Casdoor SDK 创建用户；在 `uc_tenant` 创建租户（若 `is_enterprise=true` 则 `tenant_type="ENTERPRISE", cert_level=2, cert_status=2`，否则 `PERSONAL, cert_level=0`）；在 `perm_tenant_workspace` 创建默认空间 `is_default=1`；在 `uc_user_profile` 写入 `account_type="MASTER"`；在 `perm_user_role_workspace` 授予 `PLATFORM_ADMIN`（管理员）或 `TENANT_ADMIN`（普通用户）。
* 若已存在：更新基础状态并返回已绑定的 `user_id`、`tenant_id`、`default_workspace_id`。

2. **`POST /api/v1/sync/enterprise-approved`（门户后台企业认证通过回调）**
* **入参**：`{"portal_user_id": "p_1001", "company_name": "某某科技公司", "credit_code": "91320100XXXXXXXXXX", "legal_person_name": "张三"}`
* **核心逻辑**：将对应租户的 `tenant_type` 升级为 `ENTERPRISE`，`cert_level=2`，`cert_status=2`，`tenant_name=company_name`，解锁创建企业子账号能力。

3. **`GET /api/v1/sso/exchange-iframe-session?portal_token=xxx`（`<iframe>` 嵌入页面免密换票）**
* **核心逻辑**：供 `8001/ui/*` 前端页面加载时调用。后端拿着 `portal_token` 反查门户用户信息接口（若 `PORTAL_USERINFO_URL` 未配置，开发测试期支持解析约定格式的测试 Token 或直接按参数模拟）；内部自动调用上述 **JIT 懒加载逻辑**确保用户在库中建档，签发 `8001` 内部访问 Token 返回给前端。

4. **`POST /api/v1/sso/sub-login`（企业子账号登录门户兜底接口）**
* **入参**：`{"username": "algo_dev_lisi", "password": "Password123!@#"}`
* **核心逻辑**：供门户登录接口在本地库查不到账号时调用。后端向 Casdoor 校验密码，校验通过后返回子账号的 `user_id`、`tenant_id`、`tenant_name`、`account_type="SUB"`、`roles` 及默认 `workspace_id`，使企业子账号能直接登录门户。

### 5.2 主子账号树与工作空间权限接口（`sub_account.py` & `workspace_perm.py`）

1. **`POST /api/v1/uc/sub-account/create`（创建企业子账号）**
* **前置校验**：当前操作人必须为 `TENANT_ADMIN` 且所属租户 `tenant_type == "ENTERPRISE"`。
* **入参**：`{"username": "dev_01", "password": "Pwd123!@#", "display_name": "李工", "phone": "13900001111", "parent_user_id": "usr_root_01", "workspace_ids": ["ws_default_01"], "role_code": "TENANT_DEVELOPER", "allowed_modules": ["platform:model_engine:module:*"]}`
* **核心逻辑**：调用 Casdoor SDK 建号 $\rightarrow$ 写 `uc_user_profile`（`account_type="SUB"`，拼接 `tree_path`）$\rightarrow$ 写 `perm_user_role_workspace`。

2. **`GET /api/v1/uc/sub-account/tree`**：基于 `tree_path LIKE '/usr_root/%'` 查询主账号下所有子账号及其绑定的工作空间与角色列表。
3. **`POST /api/v1/perm/workspace/create` & `GET /api/v1/perm/workspace/list`**：新建/查询当前租户下的工作空间（支持映射 `k8s_namespace`、`model_project_id`、`caict_workspace_id`）。
4. **`GET /api/v1/perm/user/modules-and-roles?platform_code=MODEL_ENGINE&workspace_id=ws_01`**：
* 返回当前用户在指定租户、工作空间和目标平台下的 `role_code`（7 大标准角色之一）及 `allowed_modules` 清单（优先读 Redis 缓存）。

### 5.3 跨平台免密 Jump Ticket 与网关签名接口（`jump_ticket.py`）

1. **`POST /api/v1/auth/jump-ticket/issue`（门户点击跳往子平台时签发票据）**
* **入参**：`{"target_platform": "MODEL_ENGINE", "workspace_id": "ws_01"}`
* **核心逻辑**：校验用户对该 `workspace_id` 的访问权 $\rightarrow$ 生成 AES-256 加密字符串 `tkt_xxx` $\rightarrow$ 写入 Redis `SETEX jump:ticket:{ticket_id} 60 "UNCONSUMED"` $\rightarrow$ 返回 `jump_ticket` 与过期时间 `60`。

2. **`POST /api/v1/auth/jump-ticket/verify`（子平台后端回调核销票据）**
* **入参**：`{"jump_ticket": "tkt_xxx", "caller_platform": "MODEL_ENGINE"}`
* **核心逻辑**：使用 Redis **`GETDEL`** 原子读取并删除票据（确保单次有效、防重放，二次调用直接返回 `HTTP 403`）$\rightarrow$ 解密校验 `target_platform == caller_platform` $\rightarrow$ 查询并返回完整上下文：`{tenant_id, tenant_type, cert_level, user_id, master_user_id, account_type, active_workspace_id, roles, allowed_modules, k8s_namespace}`。

3. **网关 Header 染色与签名工具函数（`app/core/security.py`）**：
* 严格按详设 2.3.2 节生成 9 大可信请求头：`X-Portal-Tenant-Id`、`X-Portal-User-Id`、`X-Portal-Master-Uid`、`X-Portal-Workspace-Id`、`X-Portal-Role-Code`、`X-Shadow-Account`、`X-Gateway-Timestamp`、`X-Gateway-Nonce`、`X-Gateway-Signature`（`HMAC_SHA256`）。

### 5.4 第二目标接口（`api_key.py` & `shadow_grant.py`）

1. **`POST /api/v1/perm/apikey/create` & `POST /api/v1/perm/apikey/rotate` & `POST /api/v1/perm/apikey/revoke`**：生成 `ak-prod-xxx` / 明文展示一次的 `sk-xxx`（库内仅存 SM3/SHA256 加盐哈希）、支持双 Key 24 小时平滑过渡轮换、吊销即时清理 Redis 缓存。
2. **`POST /internal/v1/perm/apikey/verify`**：供能力商店网关调用的高速验权接口（校验 Key 状态、Secret 哈希、IP 白名单、订阅 Scope 白名单）。
3. **`POST /api/v1/perm/grant/trusted-data-callback`**：接收可信数据流通交易契约签署回调，写入 `perm_service_subscription_grant` 与 `casbin_rule`，并自动追加到租户 API Key 的 `allowed_scopes`。
4. **`GET /internal/v1/uc/shadow-route/resolve`**：查询或 JIT 静默创建异构平台（如信通院 `CAICT_DATA_ENG`）影子账号映射（Redis 缓存 12 小时）。

---

## 6. 前端页面规范（挂载于 `8001/ui/*`，`<iframe>` 嵌入门户）

在 `app/main.py` 中配置静态文件挂载：将 `web_dist/` 映射至 `/ui`，所有 `/ui/*` 路由回退返回 `web_dist/index.html`。**不重做登录页、注册页、企业审核页**，仅开发以下 3 个页面：

1. **`/ui/members`（第一目标 — 企业成员与角色管理页）**：
* 顶部携带公共上下文切换条（支持多租户身份切换与当前 Workspace 下拉切换）。
* 主体展示当前企业下的主子账号列表（姓名、账号类型 `MASTER/SUB`、所属空间、角色标签、状态）。
* 点击【新建子账号】弹出抽屉：填写用户名、密码、手机号、所属上级，选择 **Workspace**，单选 **4 大租户标准角色**（`TENANT_ADMIN` / `TENANT_DEVELOPER` / `TENANT_FINANCE` / `TENANT_VIEWER`），若选研发则展示平台大模块复选框（模型工程、算力调度、Agent 工程、信通院数据工程）。

2. **`/ui/workspaces`（第一目标 — 工作空间管理页，可作为 `/ui/members` 的第二个 Tab）**：
* 展示租户下所有 Workspace 卡片/列表（区分默认空间与项目空间、显示关联的底层资源空间标识），支持【新建工作空间】。

3. **`/ui/apikeys`（第二目标 — 能力商店 API Key 凭证管理页）**：
* 展示当前租户的 `ak-prod-xxx` 列表、状态（`ACTIVE`/`ROTATING`/`REVOKED`）、过期时间；支持【新建密钥】（弹窗一次性展示明文 `sk-xxx` 并提供复制按钮）、【平滑轮换】和【吊销】。

---

## 7. 开发与纯命令行测试顺序（Step by Step）

### Step 1：工程骨架、Docker 编排与自动建表 `[全职]`

1. 生成 `Dockerfile`（Python 3.12）、`docker-compose.yml`、`requirements.txt`、`build_and_export.sh`、`build_and_export.ps1` 及 `sql/01_phase1_core.sql`。
2. 搭建 `app/` FastAPI 基础工程，启动时自动连接 `casdoor` MySQL 库执行 `01_phase1_core.sql` 建表，提供 `GET /health`。
3. **测试**：`docker compose up -d --build iam-service`，确认 `http://10.212.131.6:8001/docs` 正常打开且 MySQL `casdoor` 库生成 5 张扩展表。

### Step 2：门户旁路同步、JIT 懒加载与子账号登录兜底 `[全职]`

1. 在 Casdoor `8000` 后台配置好 Application（开启 `Password` 授权类型以便 CLI 测试）及 7 大标准角色，获取 `Client ID` 和 `Client Secret` 填入 `.env`。
2. 开发 `app/api/v1/portal_sync.py`（`sync/user`、`sync/enterprise-approved`、`exchange-iframe-session`、`sub-login`）。
3. **测试**：在 `/docs` 调用 `sync/user` 同步一个门户用户 $\rightarrow$ 调用 `sync/enterprise-approved` 升级为企业租户 $\rightarrow$ 验证库中租户已变为 `ENTERPRISE`。

### Step 3：主子账号树、Workspace 管理与权限查询 `[全职]`

1. 开发 `sub_account.py` 与 `workspace_perm.py`（创建子账号写入物化路径、创建 Workspace、查询 `modules-and-roles`）。
2. **测试**：调用接口创建 2 个不同模块权限的研发子账号 $\rightarrow$ 查询主子树列表与各自模块准入清单，确认权限隔离正确。

### Step 4：60 秒 Jump Ticket 与网关签名算法 `[全职]`

1. 开发 `jump_ticket.py`（签发 + Redis `GETDEL` 原子核销）与 `security.py` 网关 HMAC-SHA256 签名函数。
2. **测试**：签发 Ticket $\rightarrow$ 第 1 次核销返回 `200` 及完整租户/空间/角色上下文 $\rightarrow$ 第 2 次核销返回 `403`。

### Step 5：编写并运行纯命令行全链路验收脚本（第一目标封板）`[全职]`

1. 编写 `scripts/test_cli.py`，通过命令行一键串联测试：`Casdoor 获取 Token` $\rightarrow$ `门户用户同步与企业升级` $\rightarrow$ `创建子账号与分配角色` $\rightarrow$ `子账号 sub-login 登录验证` $\rightarrow$ `签发 Jump Ticket 与单次核销防重放` $\rightarrow$ `网关签名生成校验`。
2. 运行 `python scripts/test_cli.py`，所有断言 `[PASS]` 即完成第一目标后端封板。

### Step 6：前端嵌入页面开发与 `/ui/*` 联调 `[兼职]`

1. 开发 `/ui/members`（含工作空间管理 Tab），打包输出至 `web_dist/`。
2. 验证通过 `?portal_token=xxx` 访问 `http://10.212.131.6:8001/ui/members` 能自动换票加载成员列表并完成子账号创建与授权。

### Step 7：第二目标（API Key 引擎、契约回调与影子路由）`[全职 + 兼职]`

1. 执行 `sql/02_phase2_ext.sql`，开发 `api_key.py`、`shadow_grant.py` 及前端 `/ui/apikeys` 页面。
2. 补充 CLI 测试用例验证 API Key 创建、24 小时双 Key 轮换、高速验权拦截及可信数据流通契约回调。


Swagger 交互文档：http://10.214.131.6:8001/docs
ReDoc 文档页：http://10.214.131.6:8001/redoc
嵌入管理前端：http://10.214.131.6:8001/ui/members?portal_token=token_p_1794975997