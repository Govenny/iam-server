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
