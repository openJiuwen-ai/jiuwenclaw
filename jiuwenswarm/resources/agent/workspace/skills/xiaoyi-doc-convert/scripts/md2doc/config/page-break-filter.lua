local RAW_PAGEBREAK = "<w:p><w:r><w:br w:type=\"page\" /></w:r></w:p>"

local function pageBreak(el)
    if el.text == "\\newpage" then
        el.text = RAW_PAGEBREAK
        el.format = "openxml"
        return el
    end
end

return { { RawBlock = pageBreak } }