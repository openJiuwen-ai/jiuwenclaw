# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

COMPONENTS_INFO: str = """\
  - **开始**("type": "Start")：工作流的起始节点，用于初始化流程。
  - **结束**("type": "End")：工作流的结束节点。
  - **大模型**("type": "LLM")：调用大语言模型的节点，根据入参和提示词生成回复。
  - **意图识别**("type": "IntentDetection")：根据用户输入进行分类并控制流程走向不同分支进行处理。
  - **提问器**("type": "Questioner")：通过与用户对话的方式完成预配置参数的提取。
  - **代码**("type": "Code")：执行自定义python代码逻辑，无法完成主观任务。
  - **插件**("type": "Plugin")：集成预置或自定义的第三方插件，完成特定功能。
  - **输出**("type": "Output")：在工作流执行过程中输出指定内容，例如"正在执行中"等状态暂时或安抚语。
  - **选择器**("type": "Branch")：根据条件判断，决定工作流的执行路径。"""


SCHEMA_INFO: str = "\n".join([
    """\
  开始节点：工作流的起始节点，支持多个输出
  限制：
  1. outputs必须包含用户输入参数，即存在 {"name": "query", "description": "用户输入"} 元素
  2. 输出参数的type必须与后续节点（如插件、代码节点）的输入参数类型保持一致
  3. type可选值：string、integer、number、boolean、array、object
  {
    "id": "node_start",
    "type": "Start",
    "description": "工作流开始",
    "parameters": {
      "outputs": [{"name": "query", "description": "用户输入", "type": "string"}]
    },
    "next": "node_end"
  }""",
    """\
  结束节点：配置template定义最终输出的形式，支持多个输入参数
  限制：
  1. 没有输出参数，禁止其他节点引用结束节点的输出
  2. template中引用inputs的参数需要使用双括号的形式{{}}
  {
    "id": "node_end",
    "type": "End",
    "description": "工作流结束",
    "parameters": {
      "inputs": [{"name": "result", "value": "${node_start.query}"}],
      "configs": {"template": "{{result}}"}
    }
  }""",
    """\
    大模型节点：通过配置system_prompt和user_prompt调用大模型生成回复
  限制：
  1. system_prompt和user_prompt中引用inputs的参数需要使用双括号的形式{{}}
  2. output_format可选值为text、markdown或json，默认为text
  3. 当output_format为text或markdown时，outputs只能配置一个输出参数
  4. 当output_format为json时，outputs可配置多个输出参数
  {
    "id": "node_llm",
    "type": "LLM",
    "description": "调用大模型生成回复",
    "parameters": {
      "inputs": [{"name": "query", "value": "${node_start.query}"}],
      "outputs": [{"name": "output", "description": "大模型输出"}],
      "configs": {
        "system_prompt": "## 人设\\n你是一个xxx。\\n\\n## 任务描述\\nxxx",
        "user_prompt": "{{query}}",
        "output_format": "text"
      }
    },
    "next": "node_end"
  }""",
    """\
  意图识别节点：可以自定义配置prompt进行分类
  限制：
  1. conditions中expression固定只能使用contain
  2. default分支必须存在，表示其他意图。
  3. 只包含input一个输入参数，没有输出参数。
  4. configs中必须包含prompt，内容可自定义
  5. 没有输出参数，禁止其他节点引用意图识别节点的输出
  {
    "id": "node_intent_detection",
    "type": "IntentDetection",
    "description": "根据输入判断意图类别",
    "parameters": {
      "inputs": [{"name": "input", "value": "${node_start.query}"}],
      "configs": {"prompt": "你是一个功能分类器，可以根据用户的请求，结合相应的功能类别描述，帮助用户选择正确的分支"},
      "conditions": [
        {
          "branch": "branch_1",
          "description": "分类1",
          "expression": "${node_intent_detection.rawOutput} contain 分类1",
          "next": "node_llm"
        },
        {
          "branch": "branch_2",
          "description": "分类2",
          "expression": "${node_intent_detection.rawOutput} contain 分类2",
          "next": "node_llm_2"
        },
        {
          "branch": "branch_0",
          "description": "默认分支",
          "expression": "default",
          "next": "node_end"
        }
      ]
    }
  }""",
    """\
  提问器节点：配置prompt向用户提问，按照预配置的outputs参数从用户回复中提取内容。
  限制：prompt中引用inputs的参数需要使用双括号的形式{{}}
  {
    "id": "node_questioner",
    "type": "Questioner",
    "description": "提问用户的出行目的和出行方式",
    "parameters": {
      "inputs": [{"name": "input", "value": "${node_start.query}"}],
      "outputs": [{"name": "destination", "description": "出行目的地"}, {"name": "travel_mode", "description": "出行方式"}],
      "configs": {"prompt": "请输入出行目的地以及出行方式"}
    },
    "next": "node_llm"
  }""",
    """\
  代码节点：配置code自定义代码逻辑，保障代码正常运行，需要在代码区域使用转义字符
  限制：
  1. inputs参数按照`name: value`的键值对放入args.params中，代码返回结果时同样需要按照`名称: 值`的形式返回字典，且名称需要和outputs配置的参数保持一致
  2. outputs中的每个元素必须包含type字段，type可选值为：
     - string: 字符串类型
     - integer: 整数类型
     - number: 数字类型（整数或浮点数）
     - boolean: 布尔类型
     - array: 数组类型，需要额外添加items字段指定元素类型
     - object: 对象类型，需要额外添加properties和required字段
     - date-time: 日期时间类型
  3. 当type为array时，必须添加items字段，如：{"name": "list", "description": "数字列表", "type": "array", "items": {"type": "string"}}
  4. 当type为object时，必须添加properties和required字段，
     如：{"name": "config", "description": "配置对象", "type": "object", "properties": {}, "required": []}
  5. 输入参数的type必须与前置节点的输出参数类型保持一致
  {
    "id": "node_code",
    "type": "Code",
    "description": "计算BMI",
    "parameters": {
      "inputs": [
      {"name": "height", "value": "${node_questioner.height}"}, 
      {"name": "weight", "value": "${node_questioner.weight}"}
      ],
      "outputs": [
        {"name": "bmi", "description": "计算的BMI结果", "type": "number"},
        {"name": "status", "description": "状态标记", "type": "boolean"},
        {"name": "history", "description": "历史记录列表", "type": "array", "items": {"type": "string"}}
      ],
      "configs": {
      "code": (
        "def main(args: Args):\\n"
        "    '''运行代码会调用此函数\\n"
        "    :param args: 输入固定为Args对象类型，输入参数在args.params中\\n"
        "    :return: 输出参数为字典类型，kv为输出参数键值对\\n"
        "    '''\\n"
        "    h = args.params['height']\\n"
        "    w = args.params['weight']\\n"
        "    bmi = w / h / h\\n"
        "    result = {'bmi': bmi, 'status': bmi < 25, 'history': ['init']}\\n"
        "    return result"
      )}
    },
    "next": "node_llm"
  }""",
    """\
  插件节点：通过配置tool_id选取插件
  限制：
  1. tool_id需要从提供的工具资源列表中选取，并保持输入输出参数与对应资源的输入输出相同
  2. 插件节点固定输出error_code、error_message、data三个参数，后续节点只能引用data参数
  3. 插件节点的outputs配置的是工具原本的输出参数，会包含在data中返回
  4. 输入参数的type必须与工具资源的input_parameters类型保持一致
  {
    "id": "node_plugin",
    "type": "Plugin",
    "description": "调用OCR",
    "parameters": {
      "inputs": [{"name": "image_file", "value": "${node_start.input_file}"}],
      "outputs": [{"name": "text", "description": "文本识别结果"}],
      "configs": {"tool_id": "mock_ocr", "tool_name": "ocr"}
    },
    "next": "node_llm"
  }""",
    """\
  输出节点：配置template定义输出的内容形式，
  限制：
  1. template中使用双括号形式{{}}引用输入参数 
  2. 需要使用inputs中的所有参数，不能遗漏，支持零到多个输入参数
  3. 没有输出参数，禁止其他节点引用输出节点的输出
  {
    "id": "node_output",
    "type": "Output",
    "description": "输出大模型回答",
    "parameters": {
      "inputs": [{"name": "content", "value": "${node_llm.output}"}],
      "configs": {"template": "{{content}}"}
    },
    "next": "node_end"
  }""",
    """\
  选择器节点：通过对参数进行条件判断控制分支走向
  限制：
  1. 当一个条件分支只有一个条件关系时使用`"expression": "条件表达式"`，
     存在多个条件关系时使用`"expressions": ["条件表达式1", "条件表达式2"]`
  2. expression/expressions 中支持 eq/not_eq/contain/not_contain/longer_than/longer_than_or_eq/
     short_than/short_than_or_eq/is_empty/is_not_empty 十种方式条件关系
  3. operator支持and/or两种方式，只要一个条件分支存在多个条件表达式时才被使用。
  4. default分支必须存在。
  {
    "id": "node_branch",
    "type": "Branch",
    "description": "条件判断",
    "parameters": {
      "conditions": [
        {
          "branch": "branch_1",
          "description": "当值大于0且小于5时",
          "expressions": ["${node_questioner.number} longer_than 0", "${node_questioner.number} short_than 5"],
          "operator": "and",
          "next": "node_llm"
        },
        {
          "branch": "branch_2",
          "description": "当值为空时",
          "expression": "${node_questioner.number} is_empty",
          "next": "node_llm_2"
        },
        {
          "branch": "branch_0",
          "description": "默认分支",
          "expression": "default",
          "next": "node_end"
        }
      ]
    }
  }""",
])


EXAMPLES: str = """\
  - 案例 1 智能问答工作流
  设计文档输入：\\n## 任务总览\\n
  - 目标：搭建一个问答工作流，用户提问后由大模型生成回答并输出结果。\\n
  - 输入：用户提问的问题。\\n
  - 输出：大模型生成的回答。\\n\\n
  ## 整体流程\\n1. 用户输入问题。\\n2. 系统接收用户输入的问题。\\n3. 大模型根据用户输入的问题生成回答。\\n4. 系统输出大模型生成的回答。\\n\\n
  # ## 注意事项\\n1. 步骤1需要用户输入问题。\\n2. 步骤3需要调用大模型生成回答。\\n\\n
  # 用户输入：搭建一个智能客服工作流，用户输入问题后判断用户意图。当用户希望退款时，通过大模型根据公司退款政策，为用户提供退款说明。当用户进行咨询时，通过道行根据用户的问题提供相关产品咨询建议。
  # 两者都不是的时候输出'抱歉，我暂时无法理解您的请求，请尝试使用更明确的描述。'\\n设计文档：\\n## 任务总览\\n- 目标：搭建一个智能客服工作流，根据用户输入的问题判断用户意图并提供相应的服务。\\n
  # - 输入：用户输入的问题文本。\\n
  # - 输出：根据用户意图提供退款说明、产品咨询建议或无法理解的提示。\\n  
  # 生成结果：\\n  
  # ```json
  # [
  # {\"id\": \"node_start\", \"type\": \"Start\", 
  # \"description\": \"问答开始\", \"parameters\": {\"outputs\": [{\"name\": \"query\", \"description\": \"用户输入\"}]},
  #  \"next\": \"node_ask_question\"}, 
  # {\"id\": \"node_ask_question\", \"type\": \"Questioner\", 
  # \"description\": \"获取用户问题\", \"parameters\": 
  # {\"inputs\": [], \"outputs\": [{\"name\": \"question\", \"description\": \"问题\"}], 
  # \"configs\": {\"prompt\": \"请描述你想了解的问题。\"}}, \"next\": \"node_llm_answer\"}, 
  # {\"id\": \"node_llm_answer\", \"type\": \"LLM\", \"description\": \"大模型回答\", 
  # \"parameters\": {\"inputs\": [{\"name\": \"input\", \"value\": \"${node_ask_question.question}\"}],
  #  \"outputs\": [{\"name\": \"output\", \"description\": \"大模型输出\"}], 
  # \"configs\": {\"system_prompt\": \"你是一个知识助手，请回答用户的问题\", \"user_prompt\": \"问题：{{input}}\"}}, 
  # \"next\": \"node_show_result\"}, {\"id\": \"node_show_result\", \"type\": \"Output\", \"description\": \"输出结果\",
  #  \"parameters\": {\"inputs\": [{\"name\": \"content\", \"value\": \"${node_llm_answer.output}\"}], 
  # \"configs\": {\"template\": \"这是我找到的答案：{{content}}\"}}, \"next\": \"node_end\"}, 
  # {\"id\": \"node_end\", \"type\": \"End\", \"description\": \"问答结束\", 
  # \"parameters\": {\"inputs\": [], \"configs\": {\"template\": \"\"}}}]```\\n  
  # 
  # - 案例 2 智能客服流程\\n  设计文档输入：## 任务总览\\n
  # - 目标：搭建一个智能客服工作流，根据用户输入的问题判断用户意图并提供相应的服务。\\n
  # - 输入：用户输入的问题文本。\\n
  # - 输出：根据用户意图提供退款说明、产品咨询建议或无法理解的提示。\\n\\n
  # ## 整体流程\\n1. 接收用户输入的问题文本。\\n2. 判断用户意图：\\n   
  # - 如果用户希望退款，则通过大模型根据公司退款政策生成退款说明。\\n   
  # - 如果用户进行咨询，则通过道行根据用户的问题提供相关产品咨询建议。\\n   
  # - 如果两者都不是，则输出'抱歉，我暂时无法理解您的请求，请尝试使用更明确的描述。'\\n\\n
  # ## 注意事项\\n1. 需要用户输入问题文本。\\n2. 需要调用大模型生成退款说明。\\
  # 3. 需要调用道行提供产品咨询建议。\\n  
  # 生成结果：\\n 
  #  ```json
  # [
  # {\"id\": \"node_start\", \"type\": \"Start\", \"description\": \"开始\", 
  # \"parameters\": {\"outputs\": [{\"name\": \"query\", \"description\": \"用户输入\"}]}, 
  # \"next\": \"node_intent_detection\"}, 
  # {\"id\": \"node_intent_detection\", \"type\": \"IntentDetection\", 
  # \"description\": \"客户意图识别\", \"parameters\": 
  # {\"inputs\": [{\"name\": \"input\", \"value\": \"${node_start.query}\"}], 
  # \"configs\": {\"prompt\": \"你是一个功能分类器，可以根据用户的请求，结合相应的功能类别描述，帮助用户选择正确的分支\"}, 
  # \"conditions\": [{\"branch\": \"branch_1\", \"description\": \"退款\", 
  # \"expression\": \"${node_intent_detection.rawOutput} contain 退款\", 
  # \"next\": \"node_refund_handler\"}, 
  # {\"branch\": \"branch_2\", \"description\": \"咨询\", 
  # \"expression\": \"${node_intent_detection.rawOutput} contain 咨询\", 
  # \"next\": \"node_consult_handler\"}, 
  # {\"branch\": \"branch_0\", \"description\": \"默认分支\", 
  # \"expression\": \"default\", \"next\": \"node_unknown_handler\"}]}}, 
  # {\"id\": \"node_refund_handler\", \"type\": \"LLM\", \"description\": \"退款处理\", 
  # \"parameters\": {\"inputs\": [{\"name\": \"input\", \"value\": \"${node_start.query}\"}], 
  # \"outputs\": [{\"name\": \"output\", \"description\": \"退款说明\"}], \"configs\": 
  # {\"system_prompt\": \"请根据公司退款政策，为用户提供退款说明。\", \"user_prompt\": \"用户诉求：{{input}}\"}}, 
  # \"next\": \"node_end\"}, {\"id\": \"node_consult_handler\", \"type\": \"LLM\", \"description\": \"咨询处理\", 
  # \"parameters\": {\"inputs\": [{\"name\": \"input\", \"value\": \"${node_start.query}\"}], 
  # \"outputs\": [{\"name\": \"output\", \"description\": \"咨询回复\"}], \"configs\": 
  # {\"system_prompt\": \"请根据用户的问题提供相关产品咨询建议。\", \"user_prompt\": 
  # \"问题：{{input}}\"}}, \"next\": \"node_end\"}, {\"id\": \"node_unknown_handler\", 
  # \"type\": \"Output\", \"description\": \"提示不确定意图\", \"parameters\": 
  # {\"inputs\": [], \"configs\": 
  # {\"template\": \"抱歉，我暂时无法理解您的请求，请尝试使用更明确的描述。\"}}, 
  # \"next\": \"node_end\"}, {\"id\": \"node_end\", \"type\": \"End\", 
  # \"description\": \"结束\", \"parameters\": {\"inputs\": [], \"configs\": 
  # {\"template\": \"\"}}}]```\\n  - 
  # 
  # 案例 3 天气助手工作流\\n  设计文档输入：
  # ## 任务总览\\n
  # - 目标：根据用户输入的城市名查询天气，并根据温度范围生成自然语言回答。\\n
  # - 输入：城市名（字符串类型，1个）。\\n- 输出：根据温度范围生成的自然语言回答。\\n\\n
  # ## 整体流程\\n1. 用户输入城市名。\\n2. 系统根据城市名查询天气信息，获取当前温度。\\n
  # 3. 根据温度范围判断：\\n   - 如果温度低于10度，调用大模型生成提醒用户注意保暖的回答。\\n
  # - 如果温度高于30度，调用大模型生成提醒用户注意防暑降温的回答。\\n   
  # - 如果温度处于10-30度之间，调用大模型生成提醒用户温度适宜，推荐外出运动或游玩的回答。\\n
  # 4. 输出生成的自然语言回答。\\n\\n## 注意事项\\n1. 需要用户输入城市名。\\n
  # 2. 需要调用外部天气查询接口获取温度信息。\\n3. 需要调用大模型生成自然语言回答。\\n
  # 4. 业务规则：\\n   - 温度低于10度时，提醒用户注意保暖。\\n   
  # - 温度高于30度时，提醒用户注意防暑降温。\\n   
  # - 温度处于10-30度时，提醒用户温度适宜，推荐外出运动或游玩。\\n  
  # 生成结果：\\n  
  # ```json
  # [
  # {\"id\": \"node_start\", \"type\": \"Start\", \"description\": \"问答\", 
  # \"parameters\": {\"outputs\": [{\"name\": \"query\", \"description\": \"用户输入\"}]}, 
  # \"next\": \"node_ask_city\"}, {\"id\": \"node_ask_city\", \"type\": \"Questioner\", 
  # \"description\": \"提问获取城市名称\", \"parameters\": {\"inputs\": [], \"outputs\":
  #  [{\"name\": \"city\", \"description\": \"城市\"}], \"configs\": 
  # {\"prompt\": \"请输入你想查询天气的城市名。\"}}, \"next\": \"node_plugin_weather\"},
  # {\"id\": \"node_plugin_weather\", \"type\": \"Plugin\", 
  # \"description\": \"天气查询插件\", \"parameters\": {\"inputs\": 
  # [{\"name\": \"city\", \"value\": \"${node_ask_city.city}\"}], \"outputs\": 
  # [{\"name\": \"weather\", \"description\": \"天气信息\"}], \"configs\": 
  # {\"tool_id\": \"mock_weather_id\", \"tool_name\": \"get_weather_by_city\"}}, \"next\": \"node_code\"}, 
  # {\"id\": \"node_code_format\", \"type\": \"Code\", \"description\": \"格式化天气信息\", 
  # \"parameters\": {\"inputs\": [{\"name\": \"weather\", \"value\": \"${node_plugin_weather.weather}\"}], 
  # \"outputs\": [{\"name\": \"temperature\", \"description\": \"温度\"}, 
  # {\"name\": \"description\", \"description\": \"天气描述\"}], \"configs\": 
  # {\"code\": 
  # \"def main(args: dict):\\n    
  # params = args.get('params')\\n    
  # weather = params.get('weather')\\n    
  # temper = weather['temperature']\\n    
  # desc = weather['description']\\n    
  # return {\\n        'temperature': temper,\\n        'description': desc\\n    }\"}},
  #  \"next\": \"node_check_temperature\"}, 
  # {\"id\": \"node_check_temperature\", \"type\": \"Branch\", \"description\": 
  # \"温度判断\", \"parameters\": {\"conditions\": [{\"branch\": \"branch_1\", 
  # \"description\": \"当温度小于10度时\", \"expression\": \"${node_code_format.temperature} short_than 10\", 
  # \"next\": \"node_llm_cold\"}, {\"branch\": \"branch_2\", \"description\": \"当温度大于30度时\", 
  # \"expression\": \"${node_code_format.temperature} longer_than 30\", \"next\": \"node_llm_hot\"}, 
  # {\"branch\": \"branch_3\", \"description\": \"当温度大于等于10且小于等于30度时\", \"expressions\": 
  # [\"${node_code_format.temperature} longer_than_or_eq 10\", 
  # \"${node_code_format.temperature} short_than_or_eq 30\"], 
  # \"operator\": \"and\", \"next\": \"node_llm_mild\"}, {\"branch\": \"branch_0\", 
  # \"description\": \"默认分支\", \"expression\": \"default\", \"next\": \"node_end\"}]}}, 
  # {\"id\": \"node_llm_cold\", \"type\": \"LLM\", \"description\": \"调用大模型生成回复\",
  #  \"parameters\": {\"inputs\": [{\"name\": \"temperature\", \"value\": 
  # \"${node_code_format.temperature}\"}, {\"name\": \"description\", \"value\": 
  # \"${node_code_format.description}\"}], \"outputs\": [{\"name\": \"output\", 
  # \"description\": \"天气信息回复\"}], \"configs\": {\"system_prompt\": 
  # \"根据以下天气信息生成关怀提示，建议用户注意保暖：\\n温度：{{temperature}}，天气状况：
  # {{description}}\", \"user_prompt\": \"\"}}, \"next\": \"node_end\"}, 
  # {\"id\": \"node_llm_hot\", \"type\": \"LLM\", \"description\": \"调用大模型生成回复\",
  #  \"parameters\": {\"inputs\": [{\"name\": \"temperature\", \"value\": 
  # \"${node_code_format.temperature}\"}, {\"name\": \"description\", \"value\": 
  # \"${node_code_format.description}\"}], \"outputs\": [{\"name\": \"output\", 
  # \"description\": \"天气信息回复\"}], \"configs\": {\"system_prompt\": 
  # \"根据以下天气信息生成关怀提示，提醒用户注意防暑降温：\\n温度：{{temperature}}，
  # 天气状况：{{description}}\", \"user_prompt\": \"\"}}, \"next\": \"node_end\"}, 
  # {\"id\": \"node_llm_mild\", \"type\": \"LLM\", \"description\": \"调用大模型生成回复\", 
  # \"parameters\": {\"inputs\": [{\"name\": \"temperature\", \"value\": 
  # \"${node_code_format.temperature}\"}, {\"name\": \"description\", \"value\": 
  # \"${node_code_format.description}\"}], \"outputs\": [{\"name\": \"output\", 
  # \"description\": \"天气信息回复\"}], \"configs\": {\"system_prompt\": 
  # \"根据以下天气信息生成关怀提示，建议用户外出活动：\\n温度：{{temperature}}，
  # 天气状况：{{description}}\", \"user_prompt\": \"\"}}, \"next\": \"node_end\"}, 
  # {\"id\": \"node_end\", \"type\": \"End\", \"description\": \"工作流结束\", 
  # \"parameters\": {\"inputs\": [], \"configs\": {\"template\": \"\"}}}]```"""
