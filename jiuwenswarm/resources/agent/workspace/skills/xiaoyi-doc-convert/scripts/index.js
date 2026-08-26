#!/usr/bin/env node

/**
 * doc-convert技能主文件
 * 实现文档格式转换功能
 */

// 导入文档转换模块和环境变量加载器
const docConvert = require('./doc_convert.js');

/**
 * 从文件名或URL中提取源文件类型
 * @param {string} fileUrl - 文件URL或路径
 * @returns {string|null} 源文件类型（扩展名）
 */
function detectSourceType(fileUrl) {
    try {
        // 从URL中提取文件名
        const urlObj = new URL(fileUrl);
        const pathname = urlObj.pathname;
        const filename = pathname.split('/').pop();

        // 提取扩展名
        const match = filename.match(/\.([a-zA-Z0-9]+)$/);
        if (match) {
            return match[1].toLowerCase();
        }
        return null;
    } catch (e) {
        // URL解析失败，尝试直接从字符串提取
        const match = fileUrl.match(/\.([a-zA-Z0-9]+)(?:\?|$)/);
        return match ? match[1].toLowerCase() : null;
    }
}

/**
 * 主处理函数
 * @param {string} fileUrl - 文件下载链接
 * @param {string} targetType - 目标文件类型
 */
async function main(fileUrl, targetType) {
    try {
        console.error('=== 文档转换开始 ===');

        // 自动检测源文件类型
        const sourceType = detectSourceType(fileUrl);
        console.error(`检测到源文件类型: ${sourceType || '未知'}`);

        // 特殊处理：zip -> pptx 转换
        if (sourceType === 'zip' && targetType === 'pptx') {
            console.error('📝 注意：ZIP转PPTX需要zip包中包含HTML文件');
        }

        console.error(`正在转换文件格式: ${fileUrl} -> ${targetType}`);
        const convertedUrl = await docConvert.convertDocument(
            fileUrl,
            targetType,
            sourceType  // 传递源类型用于特殊处理
        );

        // 输出结果
        console.log(`✅ 文档转换完成！`);
        console.log(`📄 原始文件: ${fileUrl}`);
        console.log(`🎯 目标格式: ${targetType}`);
        console.log(`🔗 下载链接: ${convertedUrl}`);
        console.log(`\n💡 提示: 您可以直接点击链接下载转换后的文件`);

    } catch (error) {
        console.error(`❌ 文档转换失败: ${error.message}`);
        process.exit(1);
    }
}

// 命令行接口
if (require.main === module) {
    const args = process.argv.slice(2);
    if (args.length != 2) {
        process.exit(1);
    }
    // 合并所有参数作为提示
    main(args[0], args[1]).catch(error => {
        console.error(`处理失败: ${error.message}`);
        process.exit(1);
    });
}

module.exports = {main};