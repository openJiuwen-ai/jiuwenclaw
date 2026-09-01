-- 定义一个函数来处理单个块，将其从段落转换为普通文本
local function convertParaToPlain(block)
  if block.t == 'Para' then
    return pandoc.Plain(block.content)
  end
  return block
end

-- 定义一个函数来处理块列表，将其中的段落转换为普通文本
local function compactifyBlocks(blocks)
  for i, block in ipairs(blocks) do
    blocks[i] = convertParaToPlain(block)
  end
  return blocks
end

-- 定义一个函数来处理列表，将列表中的内容进行压缩
local function compactifyList(lst)
  lst.content = lst.content:map(compactifyBlocks)
  return lst
end

-- 注册列表处理函数
return {{
    BulletList = compactifyList,
    OrderedList = compactifyList
}}