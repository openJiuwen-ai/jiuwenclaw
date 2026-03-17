# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional, Tuple


def generate_idx_name(usr_id: str, scope_id: str, mem_type: str):
    """generate vector idx name"""
    return 'uid_{}_gid_{}_mtype_{}'.format(usr_id, scope_id, mem_type)


def parse_memtype_from_idx_name(idx_name: str) -> Optional[str]:
    """parse memory type from vector idx name"""
    mem_type = idx_name.split('_')[-1]
    return mem_type


def parse_memory_hit_infos(hits: list[Tuple[str, float]]) -> tuple[list[str], dict[str, float]]:
    try:
        ids = [hit[0] for hit in hits] if hits else []
        scores = {hit[0]: hit[1] for hit in hits} if hits else {}
        return ids, scores
    except Exception as e:
        raise ValueError(f"Failed to parse memory hit infos: {e}") from e
