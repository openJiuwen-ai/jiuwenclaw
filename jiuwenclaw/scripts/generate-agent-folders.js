const fs = require('fs');
const path = require('path');

const scriptDir = __dirname;
const envRoot = process.env.JIUWENCLAW_ROOT ? path.resolve(process.env.JIUWENCLAW_ROOT) : '';
const workspaceFromEnv = envRoot ? path.join(envRoot, 'workspace') : '';
const legacyWorkspace = path.join(scriptDir, '../../workspace');
const packageWorkspace = path.join(scriptDir, '../workspace');

const workspaceRoot = [workspaceFromEnv, legacyWorkspace, packageWorkspace]
  .filter(Boolean)
  .find((candidate) => fs.existsSync(candidate));

if (!workspaceRoot) {
  console.error('❌ 错误: 无法定位 workspace 目录');
  process.exit(1);
}

const agentPath = path.join(workspaceRoot, 'agent');
const outputPath = path.join(workspaceRoot, 'agent-data.json');

console.log('扫描目录:', agentPath);

try {
  if (!fs.existsSync(agentPath)) {
    console.error('❌ 错误: workspace/agent 目录不存在！');
    process.exit(1);
  }

  const isMarkdownFile = (fileName) => fileName.endsWith('.md') || fileName.endsWith('.mdx');
  const ROOT_FOLDER_KEY = '__root__';
  const folderData = {};

  const upsertFileToFolder = (folderKey, relativeFilePath) => {
    if (!folderData[folderKey]) {
      folderData[folderKey] = [];
    }
    folderData[folderKey].push({
      name: path.basename(relativeFilePath),
      path: `workspace/agent/${relativeFilePath.replace(/\\/g, '/')}`,
      isMarkdown: isMarkdownFile(relativeFilePath)
    });
  };

  const walkDirectory = (absoluteDirPath, relativeDirPath = '') => {
    const entries = fs.readdirSync(absoluteDirPath, { withFileTypes: true });
    entries.forEach((entry) => {
      const absoluteEntryPath = path.join(absoluteDirPath, entry.name);
      const relativeEntryPath = relativeDirPath
        ? path.join(relativeDirPath, entry.name)
        : entry.name;
      if (entry.isDirectory()) {
        walkDirectory(absoluteEntryPath, relativeEntryPath);
        return;
      }
      if (!entry.isFile()) {
        return;
      }
      const relativeFolderPath = path.dirname(relativeEntryPath);
      const folderKey = relativeFolderPath === '.' ? ROOT_FOLDER_KEY : relativeFolderPath.replace(/\\/g, '/');
      upsertFileToFolder(folderKey, relativeEntryPath);
    });
  };

  walkDirectory(agentPath);

  // 为了稳定输出，统一排序文件夹与文件
  const sortedFolderData = Object.keys(folderData)
    .sort((a, b) => a.localeCompare(b))
    .reduce((acc, folder) => {
      const sortedFiles = folderData[folder]
        .slice()
        .sort((a, b) => a.path.localeCompare(b.path));
      acc[folder] = sortedFiles;
      return acc;
    }, {});

  // 确保输出目录存在后写入 JSON 文件
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(sortedFolderData, null, 2));
  console.log('✅ 成功生成文件结构:', outputPath);
  console.log('📁 找到的文件夹:', Object.keys(sortedFolderData));
  
} catch (error) {
  console.error('❌ 读取目录失败:', error.message);
  fs.writeFileSync(outputPath, JSON.stringify({}, null, 2));
}