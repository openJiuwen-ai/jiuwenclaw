# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqResponseMessage, DMessageType, ResultType


def build_stream_response(message, sender_id, payload, seq, last=False):
    return DmqResponseMessage(
        type=DMessageType.OUTPUT,
        message_id=message.message_id,
        payload=payload,
        sender_id=sender_id,
        receiver_id=message.sender_id,
        seq=seq,
        last_chunk=last,
    )


def build_final_response(message, sender_id, seq):
    return build_stream_response(message, sender_id, {}, seq, last=True)


def build_batch_response(message, sender_id, result):
    return DmqResponseMessage(
        type=DMessageType.OUTPUT,
        message_id=message.message_id,
        payload=result,
        sender_id=sender_id,
        receiver_id=message.sender_id,
        seq=0,
        last_chunk=True,
    )


def build_error_response(message, sender_id, error):
    return DmqResponseMessage(
        type=DMessageType.OUTPUT,
        message_id=message.message_id,
        payload={},
        result_type=ResultType.ERROR,
        error_code=error.error_code,
        error_msg=error.message,
        sender_id=sender_id,
        receiver_id=message.sender_id,
        seq=0,
        last_chunk=True,
    )
