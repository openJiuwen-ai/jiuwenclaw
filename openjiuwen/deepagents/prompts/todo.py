# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

TODO_CREATE_DESCRIPTION_ZH = """
用于给智能体创建待办事项的工具
核心用途：创建新的待办事项
支持格式：用tasks参数传递带分隔符‘；’的任务集：
    {
        "tasks": "设计用户界面；实现接口集成；添加单元测试"
    }
核心规则：
    - 同一时间待办任务中只能有一个处于 'in_progress' 状态
    - 提供的任务集就是任务实际执行的顺序，第一个任务会自动设为'in_progress'，其余任务默认为 'pending'
"""

TODO_LIST_DESCRIPTION_ZH = """
检索并显示所有待办事项
"""

TODO_MODIFY_DESCRIPTION_ZH = """
用于给智能体的待办事项修改的工具
核心用途：修改待办事项，执行动作包含：更新（update）、删除（delete）、追加（append）、在其后插入（insert_after）、在其前插入（insert_before）
重要说明：
    - 本工具支持通过 'action' 与对应字段组合来修改待办事项
    - 若需重新规划待办事项列表，请调用 todo_write 工具
    - action 字段决定操作类型及对应的必填字段
action 支持的操作类型：
update：修改现有待办任务属性（任务 id 不可修改）：
    {
        "action": "update",
        "todos": [
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "content": "更新后的任务内容",
                "activeForm": "执行更新后的任务",
                "status": "in_progress"
            }
        ]
    }
delete：根据任务 id 删除指定待办任务：
    {
        "action": "delete",
        "ids": [
            "123e4567-e89b-12d3-a456-426614174000",
            "890e4567-e89b-12d3-a456-426614174001"
        ]
    }
append：在待办事项列表末尾新增任务（按传入顺序）：
    {
        "action": "append",
        "todos": [
            {
                "id": "456e4567-e89b-12d3-a456-426614174002",
                "content": "新任务内容",
                "activeForm": "执行新任务",
                "status": "pending"
            }
        ]
    }
insert_after：在指定任务 id 之后插入任务（按传入顺序）：
    {
        "action": "insert_after",
        "todo_data": [
            "123e4567-e89b-12d3-a456-426614174000",
            [   
                {
                    "id": "789e4567-e89b-12d3-a456-426614174003",
                    "content": "插入的任务内容",
                    "activeForm": "执行插入的任务",
                    "status": "pending"
                }
            ]
        ]
    }
insert_before：在指定任务 id 之前插入任务（按传入顺序）：
    {
        "action": "insert_before",
        "todo_data": [
            "123e4567-e89b-12d3-a456-426614174000",
            [
                {
                    "id": "012e4567-e89b-12d3-a456-426614174004",
                    "content": "插入的任务内容",
                    "activeForm": "执行插入的任务",
                    "status": "pending"
                }
            ]
        ]
    }
核心规则：
    - 同一时间只能有一个任务处于 'in_progress' 状态
    - 'update' 操作：id 字段不可修改
    - 'insert_after' 操作：id对应的目标任务状态必须为 'in_progress' 或 'pending'
    - 'insert_before' 操作：id对应的目标任务状态必须为 'pending'
"""