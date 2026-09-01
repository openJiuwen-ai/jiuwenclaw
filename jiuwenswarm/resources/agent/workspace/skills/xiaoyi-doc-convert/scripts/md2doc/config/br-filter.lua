function RawInline(el)
  if el.format == "html" and el.text:match("<br>") then
    return pandoc.LineBreak()
  end
end