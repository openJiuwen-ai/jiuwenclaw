local imagestyle = "Picture"

local function para(elem)
    -- 检查elem.content是否只有一个元素且该元素的tag为"Image"
    if #elem.content == 1 and elem.content[1].tag == "Image" then
        local image = elem.content[1]
        local image_div = pandoc.Div({})
        image_div.attr = image_div.attr or {}
        image_div.attr.attributes = image_div.attr.attributes or {}
        image_div.attr.attributes["custom-style"] = imagestyle

        image_div.content = { pandoc.Para(image) }

        return { image_div }  -- 返回包含image_div的表
    end
    return elem
end

return { { Para = para } }  -- 将 para 函数注册为 Pandoc 过滤器，处理 Para (段落) 元素。