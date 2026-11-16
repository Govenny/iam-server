#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国家工业中试基地与数智中台 统一接口规范 — 离线日数据文件导出脚本
依据规范第 3 章:
  3.2.1 用户信息-日文件 (USER_INFO_{YYYYMMDD}.000): USER_ID|USER_NAME|PROVINCE_CODE|EPARCHY_CODE
  3.2.2 用户属性信息-日文件 (USER_ATTR_{YYYYMMDD}.000): USER_ID|ATTR_TYPE|ATTR_NAME|ATTR_VALUE
  3.5.1 文件命名规范
  3.6.3 原子写入机制 (.tmp 临时文件写入完毕后再原子重命名)
"""

import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.database import SessionLocal
from app.models.tenant import UcTenant
from app.models.user_profile import UcUserProfile


def export_daily_files(target_dir: str = None):
    date_str = datetime.now().strftime("%Y%m%d")
    out_dir = target_dir or os.path.join(BASE_DIR, "upload", "user")
    os.makedirs(out_dir, exist_ok=True)

    user_info_file = os.path.join(out_dir, f"USER_INFO_{date_str}.000")
    user_info_tmp = f"{user_info_file}.tmp"
    user_attr_file = os.path.join(out_dir, f"USER_ATTR_{date_str}.000")
    user_attr_tmp = f"{user_attr_file}.tmp"

    db = SessionLocal()
    try:
        profiles = db.query(UcUserProfile).filter_by(is_deleted=0).all()
        user_lines = []
        attr_lines = []

        for p in profiles:
            tenant = db.query(UcTenant).filter_by(tenant_id=p.tenant_id).first()
            org_name = (tenant.tenant_name if tenant else None) or p.casdoor_user_id
            # 3.2.1: USER_ID|USER_NAME|PROVINCE_CODE|EPARCHY_CODE
            user_lines.append(f"{p.user_id}|{org_name}|32|3205\n")

            # 3.2.2: USER_ID|ATTR_TYPE|ATTR_NAME|ATTR_VALUE
            attr_lines.append(f"{p.user_id}|1|ACCOUNT_TYPE|{p.account_type}\n")
            if tenant:
                attr_lines.append(f"{p.user_id}|2|TENANT_TYPE|{tenant.tenant_type}\n")
                attr_lines.append(f"{p.user_id}|4|QUALIFICATION_LEVEL|LEVEL_{tenant.cert_level}\n")
                if tenant.credit_code:
                    attr_lines.append(f"{p.user_id}|2|CREDIT_CODE|{tenant.credit_code}\n")

        # 3.6.3 原子写入 USER_INFO
        with open(user_info_tmp, "w", encoding="utf-8") as f:
            f.writelines(user_lines)
        if os.path.exists(user_info_file):
            os.remove(user_info_file)
        os.rename(user_info_tmp, user_info_file)

        # 3.6.3 原子写入 USER_ATTR
        with open(user_attr_tmp, "w", encoding="utf-8") as f:
            f.writelines(attr_lines)
        if os.path.exists(user_attr_file):
            os.remove(user_attr_file)
        os.rename(user_attr_tmp, user_attr_file)

        print(f"[EXPORT OK] 成功导出国家中试基地数据中台日文件 (日期: {date_str}):")
        print(f"  -> 用户信息文件 (3.2.1): {user_info_file} ({len(user_lines)} 条记录)")
        print(f"  -> 用户属性文件 (3.2.2): {user_attr_file} ({len(attr_lines)} 条记录)")
        print(f"  -> 采用 3.6.3 .tmp 原子重命名保障机制完成落盘")
    finally:
        db.close()


if __name__ == "__main__":
    export_daily_files()
