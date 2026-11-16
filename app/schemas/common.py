import re
import random
from datetime import datetime
from typing import Generic, TypeVar, Optional, Any, List, Dict
from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")


def generate_timestamp_str() -> str:
    """Format: 'yyyy-MM-dd HH:mm:ss SSS', e.g. '2026-11-16 15:30:00 128'"""
    now = datetime.now()
    millis = now.strftime("%f")[:3]
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')} {millis}"


def generate_trans_id(timestamp_str: Optional[str] = None) -> str:
    """Format: yyyyMMddHHmmssSSS + 6-digit random, length 23, e.g. '20261116153000128952701'"""
    now = datetime.now()
    prefix = now.strftime("%Y%m%d%H%M%S") + now.strftime("%f")[:3]
    suffix = f"{random.randint(100000, 999999)}"
    return f"{prefix}{suffix}"


def get_rsp_key(name: str) -> str:
    """Convert API or schema name to _XXX_RSP, e.g. queryUserInfo -> _QUERY_USER_INFO_RSP"""
    if name.startswith("_") and name.endswith("_RSP"):
        return name
    if name.endswith("Resp") or name.endswith("Response"):
        name = re.sub(r"(Resp|Response)$", "", name)
    s = re.sub(r"([A-Z])", r"_\1", name)
    s = re.sub(r"[\/\-\s]+", "_", s).strip("_").upper()
    return f"_{s}_RSP"


def get_req_key(name: str) -> str:
    """Convert API or schema name to _XXX_REQ, e.g. queryUserInfo -> _QUERY_USER_INFO_REQ"""
    if name.startswith("_") and name.endswith("_REQ"):
        return name
    if name.endswith("Req") or name.endswith("Request"):
        name = re.sub(r"(Req|Request)$", "", name)
    s = re.sub(r"([A-Z])", r"_\1", name)
    s = re.sub(r"[\/\-\s]+", "_", s).strip("_").upper()
    return f"_{s}_REQ"


class ReservedItem(BaseModel):
    RESERVED_ID: str = Field(..., description="扩展属性标识")
    RESERVED_VALUE: str = Field(..., description="扩展属性取值")


class SysHeadReq(BaseModel):
    APP_ID: str = Field("APP_SZ_PILOT_001", description="接入标识码")
    TIMESTAMP: str = Field(default_factory=generate_timestamp_str, description="时间戳")
    TRANS_ID: str = Field(default_factory=generate_trans_id, description="流水号")
    TOKEN: Optional[str] = Field(None, description="MD5鉴权访问令牌")
    RESERVED: List[Dict[str, Any]] = Field(default_factory=list, description="保留字段")


class SysHeadResp(BaseModel):
    APP_ID: str = Field("APP_SZ_PILOT_001", description="接入标识码")
    TIMESTAMP: str = Field(default_factory=generate_timestamp_str, description="响应时间戳")
    TRANS_ID: str = Field(default_factory=generate_trans_id, description="序列号")
    RESP_CODE: str = Field("00000", description="平台/网关返回码: 00000代表成功")
    RESP_DESC: str = Field("Success", description="平台/网关返回描述")
    RESERVED: List[Dict[str, Any]] = Field(default_factory=list, description="保留字段")


class SysAttached(BaseModel):
    MEDIA_INFO: str = Field("", description="二进制媒体附加信息")


class ResponseModel(BaseModel, Generic[T]):
    """
    国家工业中试基地与数智中台 统一规范响应报文模型 (REST/JSON)
    包含 SYS_HEAD, SYS_BODY, SYS_ATTACHED，并向下兼容 code, message, data 字段。
    """
    SYS_HEAD: SysHeadResp = Field(default_factory=SysHeadResp)
    SYS_BODY: Dict[str, Any] = Field(default_factory=dict)
    SYS_ATTACHED: SysAttached = Field(default_factory=SysAttached)

    # 向下兼容历史字段
    code: int = 200
    message: str = "success"
    data: Optional[T] = None

    @model_validator(mode="before")
    @classmethod
    def populate_unified_envelope(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        data_val = values.get("data")
        code_val = values.get("code", 200)
        msg_val = values.get("message", "success")
        api_name_val = values.pop("api_name", "")

        if "SYS_HEAD" not in values or not values["SYS_HEAD"]:
            resp_code = "00000" if code_val == 200 else ("A99997" if code_val == 400 else "A99999")
            resp_desc = "Success" if resp_code == "00000" else msg_val
            values["SYS_HEAD"] = SysHeadResp(RESP_CODE=resp_code, RESP_DESC=resp_desc)

        if "SYS_ATTACHED" not in values or not values["SYS_ATTACHED"]:
            values["SYS_ATTACHED"] = SysAttached()

        if "SYS_BODY" not in values or not values["SYS_BODY"]:
            values["SYS_BODY"] = cls.build_sys_body(data_val, code_val, msg_val, api_name_val)

        return values

    @classmethod
    def build_sys_body(
        cls,
        data: Any,
        code: int = 200,
        message: str = "success",
        api_name: str = "",
    ) -> Dict[str, Any]:
        actual_biz_code = "0000" if code == 200 else str(code)
        actual_biz_msg = message or ("操作成功" if code == 200 else "操作失败")

        rsp_key = "_COMMON_RSP"
        if api_name:
            rsp_key = get_rsp_key(api_name)
        elif data is not None:
            if isinstance(data, BaseModel):
                rsp_key = get_rsp_key(type(data).__name__)
            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], BaseModel):
                rsp_key = get_rsp_key(type(data[0]).__name__ + "List")

        # Build payload with case-insensitivity
        rsp_payload: Dict[str, Any] = {
            "BIZ_CODE": actual_biz_code,
            "BIZ_MSG": actual_biz_msg,
        }

        dumped_data = data
        if isinstance(data, BaseModel):
            dumped_data = data.model_dump()
            for k, v in dumped_data.items():
                rsp_payload[k] = v
                rsp_payload[k.upper()] = v
        elif isinstance(data, dict):
            dumped_data = dict(data)
            for k, v in dumped_data.items():
                rsp_payload[k] = v
                rsp_payload[k.upper()] = v
        elif isinstance(data, list):
            dumped_list = [
                item.model_dump() if isinstance(item, BaseModel) else item
                for item in data
            ]
            rsp_payload["list"] = dumped_list
            rsp_payload["items"] = dumped_list
            dumped_data = dumped_list
        elif data is not None:
            rsp_payload["result"] = data

        return {
            rsp_key: rsp_payload,
            "BIZ_CODE": actual_biz_code,
            "BIZ_MSG": actual_biz_msg,
            "data": dumped_data,
        }


class ErrorResponseModel(BaseModel):
    SYS_HEAD: SysHeadResp = Field(default_factory=lambda: SysHeadResp(RESP_CODE="A99997", RESP_DESC="请求异常"))
    SYS_BODY: Dict[str, Any] = Field(default_factory=dict)
    SYS_ATTACHED: SysAttached = Field(default_factory=SysAttached)
    code: int = 400
    message: str = "请求异常"
    detail: Optional[Any] = None

    @model_validator(mode="before")
    @classmethod
    def populate_error_envelope(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        code = values.get("code", 400)
        message = values.get("message", "请求异常")
        detail = values.get("detail")
        resp_code = values.get("resp_code", "A99997")
        biz_code = values.get("biz_code", str(code))

        if "SYS_HEAD" not in values or not values["SYS_HEAD"]:
            values["SYS_HEAD"] = SysHeadResp(RESP_CODE=resp_code, RESP_DESC=message)
        if "SYS_ATTACHED" not in values or not values["SYS_ATTACHED"]:
            values["SYS_ATTACHED"] = SysAttached()
        if "SYS_BODY" not in values or not values["SYS_BODY"]:
            values["SYS_BODY"] = {
                "BIZ_CODE": biz_code,
                "BIZ_MSG": message,
                "detail": detail,
            }
        return values
