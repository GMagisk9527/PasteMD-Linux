-- Convert raw HTML <br> tags embedded in Markdown to Pandoc line breaks.
--
-- LLM/copied Markdown often uses <br> inside table cells. Pandoc keeps those
-- tags as RawInline HTML, which DOCX writers can drop or preserve poorly for
-- Word/WPS. A real LineBreak keeps the table intact and produces native DOCX
-- line-break markup.

local function is_html_break(text)
  local lower = text:lower()
  return lower:match("^%s*<br%s*/?>%s*$") ~= nil
    or lower:match("^%s*<br%s+[^>]*>%s*$") ~= nil
end

function RawInline(el)
  if el.format == "html" and is_html_break(el.text) then
    return pandoc.LineBreak()
  end
end
