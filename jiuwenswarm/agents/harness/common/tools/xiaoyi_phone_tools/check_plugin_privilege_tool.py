from __future__ import annotations

import asyncio
import json
from typing import Any

from openjiuwen.core.foundation.tool import tool

from jiuwenswarm.common.utils import logger

from .utils import execute_device_command, raise_if_device_error


INTENT_PERMISSION_MAP: dict[str, list[str]] = {
    "GetCurrentLocation": [
        "ohos.permission.LOCATION",
        "ohos.permission.APPROXIMATELY_LOCATION",
    ],
    "SearchCalendarEvent": ["ohos.permission.READ_WHOLE_CALENDAR"],
    "CreateCalendarEvent": ["ohos.permission.WRITE_WHOLE_CALENDAR"],
    "DeleteCalendarEvent": ["ohos.permission.WRITE_WHOLE_CALENDAR"],
    "ModifyCalendarEvent": ["ohos.permission.WRITE_WHOLE_CALENDAR"],
    "SearchNote": ["ohos.permission.READ_NOTE"],
    "CreateNote": ["ohos.permission.WRITE_NOTE"],
    "ModifyNote": ["ohos.permission.WRITE_NOTE"],
    "SearchContactLocal": ["ohos.permission.READ_CONTACTS"],
    "SearchPhotoVideo": ["ohos.permission.READ_IMAGEVIDEO"],
    "SaveMediaToGallery": ["ohos.permission.WRITE_IMAGEVIDEO"],
    "SearchFile": ["ohos.permission.FILE_ACCESS_MANAGER"],
    "SaveFileToFileManager": ["ohos.permission.FILE_SAVE_MANAGER"],
    "SearchAlarm": ["ohos.permission.READ_ALARM"],
    "CreateAlarm": ["ohos.permission.WRITE_ALARM"],
    "ModifyAlarm": ["ohos.permission.WRITE_ALARM"],
    "DeleteAlarm": ["ohos.permission.WRITE_ALARM"],
    "SearchMessage": ["ohos.permission.READ_SMS"],
    "SendShortMessage": ["ohos.permission.SEND_MESSAGES"],
    "StartCall": ["ohos.permission.PLACE_CALL"],
}


def build_check_plugin_privilege_command(
    check_intent_name: str,
    permission_ids: list[str],
) -> dict[str, Any]:
    return {
        "header": {
            "namespace": "Common",
            "name": "Action",
        },
        "payload": {
            "cardParam": {},
            "executeParam": {
                "achieveType": "INTENT",
                "actionResponse": True,
                "bundleName": "com.huawei.hmos.vassistant",
                "dimension": "",
                "executeMode": "background",
                "intentName": "CheckPlugInPrivilege",
                "intentParam": {
                    "checkIntentName": check_intent_name,
                    "permissionId": permission_ids,
                },
                "needUnlock": False,
                "permissionId": [],
                "timeOut": 5,
            },
            "needUploadResult": True,
            "pageControlRelated": False,
            "responses": [
                {
                    "displayText": "",
                    "resultCode": "",
                    "ttsText": "",
                }
            ],
        },
    }


def ensure_plugin_privilege_granted(
    check_intent_name: str,
    outputs: dict[str, Any],
) -> None:
    """Reject explicit privilege denial and device-side error results."""
    if not isinstance(outputs, dict):
        raise RuntimeError(
            f"Plugin privilege check returned an invalid result: {check_intent_name}"
        )

    for field_name in ("authorized", "granted"):
        if field_name not in outputs:
            continue
        value = outputs[field_name]
        normalized = str(value).strip().lower()
        if value is False or value == 0 or normalized in {
            "false",
            "0",
            "denied",
            "deny",
            "no",
        }:
            raise RuntimeError(
                f"Plugin privilege was denied for intent: {check_intent_name}"
            )

    raise_if_device_error(
        outputs,
        f"Plugin privilege check failed for intent {check_intent_name}",
    )


async def execute_plugin_privilege_check(
    check_intent_name: str,
) -> dict[str, Any]:
    """Execute the OpenClaw-compatible privilege command."""
    permission_ids = INTENT_PERMISSION_MAP.get(check_intent_name)
    if permission_ids is None:
        raise ValueError(f"Unsupported device intent: {check_intent_name}")

    command = build_check_plugin_privilege_command(
        check_intent_name=check_intent_name,
        permission_ids=permission_ids,
    )
    logger.info(
        "[CRON_DEVICE] phase=PRIVILEGE_CHECK_BEGIN intent_name=%s",
        check_intent_name,
    )
    try:
        outputs = await execute_device_command(
            "CheckPlugInPrivilege",
            command,
            timeout=60.0,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"检查插件权限超时（60秒）, intentName: {check_intent_name}"
        ) from exc
    logger.info(
        "[CRON_DEVICE] phase=PRIVILEGE_CHECK_DONE intent_name=%s "
        "retErrCode=%r errMsg=%r authorized=%r granted=%r outputs=%r",
        check_intent_name,
        outputs.get("retErrCode"),
        outputs.get("errMsg"),
        outputs.get("authorized"),
        outputs.get("granted"),
        outputs,
    )
    return outputs


@tool(
    name="check_plugin_privilege",
    description=(
        "定时任务权限检查工具。"
        "〖使用场景〗仅在创建定时任务时使用，严禁在其他场景调用。当识别到定时任务中需要使用用户设备侧工具时，必须调用此工具进行权限检查。"
        "〖调用前提〗调用此工具前，必须确认用户定时任务中提到的工具在当前模型可使用的工具列表中存在。如果当前工具列表中不存在符合用户诉求的工具定义，则不要调用此工具，而是直接告知用户当前设备不支持该功能。"
        "〖支持的意图名称及对应权限〗"
        "GetCurrentLocation（获取用户位置）, "
        "SearchCalendarEvent（搜索用户日程）, "
        "CreateCalendarEvent（新建用户日程）, "
        "DeleteCalendarEvent（删除用户日程）, "
        "ModifyCalendarEvent（修改用户日程）, "
        "SearchNote（搜索用户备忘录）, "
        "CreateNote（新建用户备忘录）, "
        "ModifyNote（修改用户备忘录）, "
        "SearchContactLocal（搜索用户联系人）, "
        "SearchPhotoVideo（搜索用户图库照片或视频）, "
        "SaveMediaToGallery（保存图片/视频到图库）, "
        "SearchFile（搜索用户文件管理里面的文件）, "
        "SaveFileToFileManager（保存文件到文件管理）, "
        "SearchAlarm（搜索闹钟）, "
        "CreateAlarm（新建闹钟）, "
        "ModifyAlarm（修改闹钟）, "
        "DeleteAlarm（删除闹钟）, "
        "SearchMessage（搜索短信）, "
        "SendShortMessage（发送短信）, "
        "StartCall（打电话）。"
        "〖多次调用〗如果用户的定时任务指令中涉及多个端侧工具，则依次分别调用此工具检查每个工具的权限。如果调用超时失败，最多重试一次。"
        "〖回复约束〗如果工具返回没有授权或其他报错，只需要完整描述没有授权或其他报错内容即可，不需要主动给用户提供解决方案。"
        "〖使用约束1〗只要是创建定时任务且涉及端插件的使用，则必须调用此工具检查权限"
        "〖使用约束2〗如果是定时任务执行过程中，禁止调用此工具，此工具仅在创建定时任务时按需调用"
    ),
)
async def check_plugin_privilege(checkIntentName: str) -> dict[str, Any]:
    permission_ids = INTENT_PERMISSION_MAP.get(checkIntentName)
    if permission_ids is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"不支持的工具意图名称: {checkIntentName}。"
                        "请确认该意图名称在支持列表中。"
                    ),
                }
            ]
        }

    outputs = await execute_plugin_privilege_check(checkIntentName)

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(outputs, ensure_ascii=False),
            }
        ]
    }
