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
