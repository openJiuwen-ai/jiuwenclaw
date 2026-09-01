-- 定义默认的表格样式
local DEFAULT_TABLE_STYLE = "TableCustom"

-- 处理表格元素的函数
function Table(elem)
    -- 确保 elem 和 elem.attr.attributes 存在
    if not elem or not elem.attr or not elem.attr.attributes then
        return elem
    end

    -- 获取属性表的引用，减少重复访问
    local attributes = elem.attr.attributes

    -- 如果 custom-style 属性不存在，则设置为默认样式
    if attributes["custom-style"] == nil then
        attributes["custom-style"] = DEFAULT_TABLE_STYLE
    end

    return elem
end