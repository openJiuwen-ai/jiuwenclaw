const { spawn } = require('child_process');
const path = require('path');

// 运行生成脚本
function generate() {
  console.log('📁 检测到文件变化，重新生成文件夹列表...');
  const child = spawn('node', [path.join(__dirname, 'generate-agent-folders.js')], {
    stdio: 'inherit'
  });
  
  child.on('close', (code) => {
    if (code === 0) {
      console.log('✅ 文件夹列表已更新\n');
    }
  });
}

// 初始生成
generate();

// 监听 workspace/agent 目录的变化
const chokidar = require('chokidar');
const watcher = chokidar.watch(path.join(__dirname, '../../workspace/agent'), {
  persistent: true,
  ignoreInitial: true
});

watcher
  .on('add', generate)
  .on('addDir', generate)
  .on('unlink', generate)
  .on('unlinkDir', generate);

console.log('👀 正在监听 workspace/agent 目录的变化...');