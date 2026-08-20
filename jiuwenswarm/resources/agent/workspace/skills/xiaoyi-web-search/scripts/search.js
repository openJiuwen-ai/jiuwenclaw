#!/usr/bin/env node

/**
 * 小艺联网搜索 - 华为云 AI 联网搜索 API
 * 根据 SKILL.md 说明实现
 *
 * 用法:
 *   node search.js "搜索关键词"
 *   node search.js "关键词" -n 10
 */

const axios = require('axios');
const fs = require('fs');

const PERSONAL_API_KEY = 'SK-815C16E8E815B128EA6080A2A5B868BE';
const PERSONAL_UID = '420086000107623357';
const SERVICE_URL = 'http://10.33.87.20:18449';

/**
 * 执行联网搜索
 * @param {string} query - 搜索关键词
 * @param {number} count - 返回结果数量（默认10，最大建议不超过20）
 * @returns {Promise<Array>} 搜索结果数组
 */
async function webSearch(query, count = 10) {
  try {

	// 网络请求
    const API_URL = SERVICE_URL + '/celia-claw/v1/rest-api/skill/execute';
	console.log(`✅ 请求url存在`);

	const requestBody = {
						  query: query,
						  count: Math.min(count, 20) // 限制最大20条
						};
	const headers = {
					  'Content-Type': 'application/json',
					  'x-skill-id': 'big_search',
					  'x-hag-trace-id': 'rytest001',
					  'x-request-from': 'openclaw',
					  'x-api-key': PERSONAL_API_KEY,
					  'x-uid': PERSONAL_UID					};

    const timeout = 10000;
    const response = await axios.post(
        API_URL,
        requestBody,
        {
          headers: headers,
          timeout: timeout
        }
    );

    // 返回搜索结果
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
        console.error('⚠️ Token 可能已过期，请更新 Token');
      }
    } else if (error.request) {
      console.error('❌ 网络错误: 无法连接到华为云 API');
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
