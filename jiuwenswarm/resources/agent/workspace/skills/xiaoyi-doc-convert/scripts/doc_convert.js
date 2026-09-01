/**
 * 文档转化 Skill
 * 提供文档格式转换功能
 */

const axios = require('axios');
const { getEnvConfig } = require('./env_loader');

// 加载环境变量配置
const envConfig = getEnvConfig();

// 配置常量（从环境变量文件获取）
const CONFIG = {
    // 鉴权配置
    SERVICE_URL: envConfig['SERVICE_URL'] || envConfig['SERVICE-URL'],
    PERSONAL_UID: envConfig['PERSONAL_UID'] || envConfig['PERSONAL-UID'],
    PERSONAL_API_KEY: envConfig['PERSONAL_API_KEY'] || envConfig['PERSONAL-API-KEY'],
    // 流式响应模式：true为流式，false为流式，true
    STREAM_MODE: envConfig['STREAM_MODE'] !== undefined ? envConfig['STREAM_MODE'] === 'true' : true,
    CONVERT_SKILL_ID: envConfig['CONVERT_SKILL_ID'],
};

// 固定值
const FIXED_VALUES = {
    SKILL_ID: 'xiaoyi_doc_convert',
    STREAM_SKILL_ID: 'xiaoyi_doc_convert_stream',
    REQUEST_FROM: 'openclaw',
    API_PATH: '/celia-claw/v1/rest-api/skill/execute',
    STREAM_API_PATH: '/celia-claw/v1/sse-api/skill/execute'
};

/**
 * 生成唯一的HAG Trace ID
 * @returns {string} 唯一的trace id
 */
function generateHagTraceId() {
    const timestamp = Date.now().toString(36);
    const random = Math.random().toString(36).substr(2, 9);
    return `hag-${timestamp}-${random}`;
}

/**
 * 发送HTTP请求（新鉴权方式）
 * @param {string} filePath - 文件URL
 * @param {string} targetType - 目标类型
 * @param {object} extraArgs - 额外参数（如 mime_type, input_filter 等）
 * @returns {Promise<object>} 响应数据
 */
async function sendRequest(filePath, targetType, extraArgs = {}) {
    // 构建完整的请求URL
    const baseUrl = CONFIG.SERVICE_URL.replace(/\/$/, ''); // 去除末尾的斜杠

    var sn = generateHagTraceId();
    // 构建请求体
    const body = {
        sn: sn,
        operator: 'doc_convert',
        argument: {
            file_path: filePath,
            target_type: targetType,
            ...extraArgs
        }
    };

    // 构建请求头
    const headers = {
        'Content-Type': 'application/json',
        'x-skill-id': CONFIG.CONVERT_SKILL_ID || FIXED_VALUES.SKILL_ID,
        'x-hag-trace-id': sn,
        'x-request-from': FIXED_VALUES.REQUEST_FROM,
        'x-uid': CONFIG.PERSONAL_UID,
        'x-api-key': CONFIG.PERSONAL_API_KEY
    };

    // 验证必要的配置
    if (!CONFIG.PERSONAL_UID) {
        throw new Error('缺少PERSONAL-UID配置，请设置环境变量PERSONAL-UID');
    }

    if (!CONFIG.PERSONAL_API_KEY) {
        throw new Error('缺少PERSONAL-API-KEY配置，请设置环境变量PERSONAL-API-KEY');
    }

    if (!CONFIG.SERVICE_URL) {
        throw new Error('缺少SERVICE_URL配置，请设置环境变量SERVICE_URL');
    }

    try {
        if (CONFIG.STREAM_MODE) {
            const apiUrl = `${baseUrl}${FIXED_VALUES.STREAM_API_PATH}`;
            headers['x-skill-id'] = CONFIG.CONVERT_SKILL_ID || FIXED_VALUES.STREAM_SKILL_ID;
            // 流式模式
            console.error(`使用流式模式请求: ${apiUrl}`);
            const response = await axios.post(apiUrl, body, {
                headers,
                timeout: 300000, // 5分钟超时
                responseType: 'stream' // 设置为流式响应
            });

            // 处理流式响应
            return new Promise((resolve, reject) => {
                let buffer = '';
                let hasValidData = false;

                response.data.on('data', (chunk) => {
                    buffer += chunk.toString();

                    // 按行分割处理
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // 保留最后一个不完整的行

                    for (const line of lines) {
                        if (!line.trim()) continue;
                        if (line === "" || !line.startsWith("data:")) {
                            continue; // 如果在循环中，跳过本次迭代
                        }
                        try {
                            // slice(5) 对应 Go 的 [5:]，去掉前 5 个字符 "data:"
                            const frame = JSON.parse(line.slice(5).trim());
                            // 只处理数据帧（最后一帧）
                            if (frame.event === 'data' && frame.content) {
                                hasValidData = true;
                                // content是字符串化的JSON，需要再次解析
                                const contentData = JSON.parse(frame.content);
                                resolve(contentData);
                            }
                        } catch (parseError) {
                            console.error(`解析帧数据失败: ${line}`, parseError.message);
                        }
                    }
                });

                response.data.on('end', () => {
                    // 处理缓冲区剩余的数据
                    if (buffer.trim()) {
                        try {
                            const frame = JSON.parse(buffer);
                            if (frame.event === 'data' && frame.content) {
                                hasValidData = true;
                                const contentData = JSON.parse(frame.content);
                                resolve(contentData);
                                return;
                            }
                        } catch (parseError) {
                            console.error(`解析最后帧数据失败: ${buffer}`, parseError.message);
                        }
                    }
                    // 流结束但没有收到有效数据，返回报错
                    if (!hasValidData) {
                        reject(new Error('流式响应结束但未收到有效数据'));
                    }
                });

                response.data.on('error', (error) => {
                    reject(new Error(`流式响应错误: ${error.message}`));
                });
            });
        } else {
            // 非流式模式
            const apiUrl = `${baseUrl}${FIXED_VALUES.API_PATH}`;
            console.error(`使用非流式模式请求: ${apiUrl}`);
            const response = await axios.post(apiUrl, body, {
                headers,
                timeout: 300000 // 5分钟超时
            });
            return response.data;
        }
    } catch (error) {
        if (error.response) {
            // 服务器响应了错误状态码
            console.error(`请求失败，状态码: ${error.response.status}`);
            console.error(`响应数据:`, error.response.data);
            throw new Error(`请求失败: ${error.response.status} - ${JSON.stringify(error.response.data)}`);
        } else if (error.request) {
            // 请求已发送但没有收到响应
            console.error(`请求发送但未收到响应: ${error.message}`);
            throw new Error(`网络请求失败: ${error.message}`);
        } else {
            // 请求配置出错
            console.error(`请求配置错误: ${error.message}`);
            throw new Error(`请求配置错误: ${error.message}`);
        }
    }
}

/**
 * 文档格式转换
 * @param {string} filePath - 文件URL
 * @param {string} targetType - 目标类型 (xlsx, pdf, docx等)
 * @param {string} sourceType - 源文件类型 (可选，用于特殊处理如 zip->pptx)
 * @returns {Promise<string>} 转换后的文件链接
 */
async function convertDocument(filePath, targetType, sourceType = null) {
    console.error(`开始文档转换: ${filePath} -> ${targetType}`);

    try {
        // 构建额外参数
        let extraArgs = {};

        // 特殊处理：zip (HTML) -> pptx 转换
        if (sourceType === 'zip' && targetType === 'pptx') {
            extraArgs = {
                mime_type: 'application/zip',
                input_filter: 'impress_pdf_import'
            };
            console.error('检测到ZIP转PPTX请求，使用特殊参数:', extraArgs);
        }

        // 使用新的鉴权方式
        const response = await sendRequest(filePath, targetType, extraArgs);

        // 解析响应，支持多种响应格式
        let convertedUrl = null;

        if (response && response.result) {
            // 格式1: result字段包含url:格式
            const urlMatch = response.result.match(/url:\s*(https?:\/\/[^\s]+)/);
            if (urlMatch && urlMatch[1]) {
                convertedUrl = urlMatch[1];
            }
            // 格式2: result字段直接是URL
            else if (response.result.startsWith('http')) {
                convertedUrl = response.result;
            }
        }

        // 格式3: 响应直接包含url字段
        if (!convertedUrl && response && response.url) {
            convertedUrl = response.url;
        }

        // 格式4: 响应包含data字段，其中包含url
        if (!convertedUrl && response && response.data && response.data.url) {
            convertedUrl = response.data.url;
        }

        if (convertedUrl) {
            console.error(`文档转换成功，获取到URL: ${convertedUrl}`);
            return convertedUrl;
        } else {
            console.error('无法从响应中提取文件链接，响应数据:', JSON.stringify(response, null, 2));
            throw new Error('无法从响应中提取文件链接');
        }
    } catch (error) {
        console.error(`文档转换失败: ${error.message}`);
        throw error;
    }
}

// 导出模块
module.exports = {
    convertDocument,
    sendRequest,
    generateHagTraceId,
    CONFIG,
    FIXED_VALUES
};