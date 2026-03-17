# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


DEFAULT_INNER_LOG_CONFIG = {
                'level': 'INFO',
                'output': ['console', 'file'],
                'log_path': './logs/',
                'log_file': 'run/jiuwen.log',
                'interface_log_file': 'interface/jiuwen_interface.log',
                'interface_output': ['console', 'file'],
                'prompt_builder_interface_log_file': 'interface/jiuwen_prompt_builder_interface.log',
                'performance_log_file': 'performance/jiuwen_performance.log',
                'performance_output': ['console', 'file'],
                'backup_count': 20,
                'max_bytes': 20971520,
                'format': ('%(asctime)s | %(log_type)s | %(filename)s | %(lineno)d | '
                          '%(funcName)s | %(trace_id)s | %(levelname)s | %(message)s')
            }

DEFAULT_LOG_CONFIG = {
    'logging': DEFAULT_INNER_LOG_CONFIG
}
