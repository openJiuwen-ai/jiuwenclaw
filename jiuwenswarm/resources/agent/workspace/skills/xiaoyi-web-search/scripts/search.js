#!/usr/bin/env node

/**
 * 小艺联网搜索 - 华为云 AI 联网搜索 API
 * 根据 SKILL.md 说明实现
 *
 * 用法:
 *   node search.js "搜索关键词"
 *   node search.js "关键词" -n 10
 *
 * 桌面形态（默认）：SERVICE_URL=np://claw-skill —— 请求经主进程 skill API 代理
 * （Windows 命名管道）转发，businessCredential 鉴权由主进程注入，本 skill 零业务
 * 凭证，仅需 CLAW_SKILL_TOKEN 本机防借用令牌（主进程经 AgentServer 环境注入）。
 *
 * 旧沙箱形态（兼容）：SERVICE_URL=http(s)://... 时直连云端，鉴权头
 * （x-api-key/x-uid）从 .xiaoyienv 读取（ACP2SERVICE_ENV 可指定路径）。
 */

const axios = require('axios');
const fs = require('fs');
const crypto = require('crypto');

/** 桌面形态默认接入点：主进程 skill API 代理（命名管道，authority 段即管道名） */
const DEFAULT_SERVICE_URL = 'np://claw-skill';
const SKILL_EXECUTE_PATH = '/celia-claw/v1/rest-api/skill/execute';

/**
 * 读取 .xiaoyienv 文件并解析为键值对象（仅旧沙箱形态需要；文件不存在返回 {}）
 * @param {string} filePath - 文件路径
 * @returns {Object} 解析后的属性对象
 */
function readXiaoyiEnv(filePath) {
  const result = {};

  try {
    const content = fs.readFileSync(filePath, 'utf8');
    const lines = content.split(/\r?\n/);

    lines.forEach(line => {
      if (!line || line.trim() === '' || line.trim().startsWith('#') || line.trim().startsWith('!')) {
        return;
      }
      // 只取第一个等号（防止值中也有 =）；key 中的 "-" 归一为 "_"（与 Python 侧 config.py 同口径）
      const [key, ...valueParts] = line.split('=');
      const value = valueParts.join('=');
      if (key && value !== undefined) {
        result[key.trim().replace(/-/g, '_')] = value.trim();
      }
    });
  } catch (err) {
    if (err.code !== 'ENOENT') {
      console.error('⚠️ 读取 .xiaoyienv 失败：', err.message);
    }
  }

  return result;
}

/** 加载配置：环境变量优先，.xiaoyienv（可选）兜底。桌面形态零配置文件。 */
function loadConfig() {
  const envPath = process.env.ACP2SERVICE_ENV || '';
  const fileEnv = envPath ? readXiaoyiEnv(envPath) : {};
  const pick = (key) => process.env[key] || fileEnv[key] || '';
  return {
    serviceUrl: pick('SERVICE_URL') || DEFAULT_SERVICE_URL,
    skillToken: pick('CLAW_SKILL_TOKEN'),
    // 旧沙箱形态鉴权（桌面形态为空即可，代理由主进程注入鉴权）
    apiKey: pick('PERSONAL_API_KEY'),
    uid: pick('PERSONAL_UID')
  };
}

/** np://claw-skill → \\\\.\\pipe\\claw-skill（authority 段即管道名） */
function pipePathFromUrl(url) {
  const name = url.slice(5).split('/')[0].trim();
  if (!name) throw new Error(`np:// URL 缺少管道名: ${url}`);
  return `\\\\.\\pipe\\${name}`;
}

/**
 * 执行联网搜索
 * @param {string} query - 搜索关键词
 * @param {number} count - 返回结果数量（默认10，最大建议不超过20）
 * @returns {Promise<Array>} 搜索结果数组
 */
async function webSearch(query, count = 10) {
  try {
    const config = loadConfig();
    const isPipe = config.serviceUrl.toLowerCase().startsWith('np://');

    const requestBody = {
      query: query,
      count: Math.min(count, 20) // 限制最大20条
    };
    const headers = {
      'Content-Type': 'application/json',
      'x-skill-id': 'big_search',
      'x-hag-trace-id': crypto.randomUUID(),
      'x-request-from': 'openclaw'
    };

    let response;
    if (isPipe) {
      // 桌面形态：HTTP over 命名管道（axios socketPath），鉴权主进程注入，
      // 仅携带 skillToken 本机防借用令牌
      if (!config.skillToken) {
        console.error('❌ CLAW_SKILL_TOKEN 环境变量未设置（桌面形态由主进程注入）');
        return [];
      }
      headers['Authorization'] = `Bearer ${config.skillToken}`;
      response = await axios.post(`http://claw-skill${SKILL_EXECUTE_PATH}`, requestBody, {
        socketPath: pipePathFromUrl(config.serviceUrl),
        headers: headers,
        timeout: 30000,
        // 管道是本地通道：禁用 axios 对环境代理变量的拾取（防御）
        proxy: false
      });
    } else {
      // 旧沙箱形态：直连云端，x-api-key/x-uid 鉴权
      if (!config.apiKey || !config.uid) {
        console.error('❌ 旧沙箱形态缺少 PERSONAL_API_KEY / PERSONAL_UID 配置（.xiaoyienv）');
        return [];
      }
      headers['x-api-key'] = config.apiKey;
      headers['x-uid'] = config.uid;
      response = await axios.post(`${config.serviceUrl.replace(/\/+$/, '')}${SKILL_EXECUTE_PATH}`, requestBody, {
        headers: headers,
        timeout: 10000
      });
    }

    const data = response.data;

    if (data.code !== 0) {
      console.error(`❌ API 错误: ${data.msg || '未知错误'}`);
      return [];
    }
    return data.webResult || [];
  } catch (error) {
    if (error.response) {
      console.error(`❌ API 请求失败: ${error.response.status} - ${error.response.statusText}`);
      if (error.response.status === 401) {
        console.error('⚠️ 鉴权失败：请确认已登录小艺Work（凭据由主进程管理）');
      }
    } else if (error.request) {
      console.error(`❌ 网络错误: 无法连接到服务（${error.code || error.message}）`);
    } else {
      console.error(`❌ 错误: ${error.message}`);
    }
    return [];
  }
}

/**
 * 格式化输出搜索结果
 * @param {Array} results - 搜索结果数组
 * @param {string} query - 搜索关键词
 */
function formatResults(results, query) {
  if (!results || results.length === 0) {
    console.log(`🔍 搜索 "${query}" 未找到结果`);
    return;
  }

  console.log(`\n🔍 搜索结果: "${query}"`);
  console.log(`✅ 找到 ${results.length} 条相关结果\n`);
  console.log('='.repeat(80));

  results.forEach((item, index) => {
    console.log(`\n📌 ${index + 1}. ${item.title || 'N/A'}`);
    console.log(`🔗 ${item.url || 'N/A'}`);

    if (item.chunk) {
      const snippet = item.chunk.length > 1000 ? item.chunk.substring(0, 1000) + '...' : item.chunk;
      console.log(`📝 ${snippet}`);
    }

    if (item.siteName) {
      console.log(`🏷️ 来源: ${item.siteName}`);
    }

    console.log('-'.repeat(80));
  });

  console.log(`\n💡 共找到 ${results.length} 条相关结果`);
}

/**
 * 解析命令行参数
 */
function parseArgs() {
  const args = process.argv.slice(2);
  const options = {
    query: '',
    count: 10
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];

    if (arg === '-n' || arg === '--count') {
      const next = args[i + 1];
      if (next && !next.startsWith('-')) {
        options.count = parseInt(next, 10);
        i++;
      }
    } else if (!arg.startsWith('-')) {
      options.query = arg;
    }
  }

  return options;
}

// 主程序
async function main() {
  const options = parseArgs();

  if (!options.query) {
    console.log('小艺联网搜索 - 华为云 AI 联网搜索');
    console.log('');
    console.log('用法:');
    console.log('  node search.js "搜索关键词"              # 默认10条结果');
    console.log('  node search.js "关键词" -n 5            # 返回5条结果');
    console.log('');
    console.log('示例:');
    console.log('  node search.js "人工智能最新进展"');
    console.log('  node search.js "ChatGPT 新闻" -n 10');
    process.exit(0);
  }

  const results = await webSearch(options.query, options.count);
  formatResults(results, options.query);
}

// 导出函数供外部调用
module.exports = { webSearch };

// 如果直接运行则执行主程序
if (require.main === module) {
  main();
}
