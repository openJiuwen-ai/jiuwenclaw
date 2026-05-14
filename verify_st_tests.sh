#!/bin/bash
# ST测试验证脚本

echo "🔍 JiuwenClaw ST测试验证脚本"
echo "======================================"
echo ""

# 检查ST测试目录
echo "📁 检查ST测试目录..."
if [ -d "tests/system_tests" ]; then
    echo "✅ tests/system_tests/ 目录存在"
else
    echo "❌ tests/system_tests/ 目录不存在"
    exit 1
fi

echo ""

# 检查ST测试文件
echo "📄 检查ST测试文件..."
st_files=$(find tests/system_tests/ -name "test_*.py" -type f)
if [ -n "$st_files" ]; then
    echo "✅ 发现ST测试文件:"
    echo "$st_files" | while read file; do
        echo "  - $file"
    done
    echo "📊 总计: $(echo "$st_files" | wc -l) 个ST测试文件"
else
    echo "❌ 未找到ST测试文件"
    exit 1
fi

echo ""

# 检查CI配置
echo "🔧 检查gitcode CI配置..."
if [ -f ".gitcode/workflows/ci.yml" ]; then
    echo "✅ .gitcode/workflows/ci.yml 配置文件存在"
    if grep -q "system-test" .gitcode/workflows/ci.yml; then
        echo "✅ CI配置包含system-test阶段"
    else
        echo "⚠️  CI配置可能缺少system-test阶段"
    fi
else
    echo "❌ .gitcode/workflows/ci.yml 配置文件不存在"
fi

echo ""

# 验证pytest配置
echo "🧪 验证pytest配置..."
if grep -q "system_tests" pytest.ini; then
    echo "✅ pytest.ini包含system_tests路径"
else
    echo "⚠️  pytest.ini可能缺少system_tests路径"
fi

echo ""

# 尝试收集ST测试
echo "🎯 收集ST测试..."
collected=$(python -m pytest tests/system_tests/ --collect-only --override-ini="addopts=" 2>&1 | grep "collected")
echo "📊 $collected"

echo ""
echo "======================================"
echo "✅ ST测试验证完成!"
echo ""
echo "CI系统应该能够识别并运行ST测试了。"
echo "如果仍然显示NOT_FOUND，请检查:"
echo "1. CI系统是否使用.gitcode/workflows/ci.yml"
echo "2. CI配置中的测试路径是否正确"
echo "3. tests/system_tests/目录是否在CI环境中可访问"
