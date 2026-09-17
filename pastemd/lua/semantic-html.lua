-- 语义恢复过滤器（HTML 解析阶段运行）。
-- 恢复 pandoc HTML reader 会丢弃的三类信息：
--   1. 公式源码：<span role="math" data-math-source="…"> 或
--      class="math-inline/block/display" + copy-text 属性（pandoc 会把
--      data-math-source 归一化为 math-source）→ 直接产出 Math 元素，
--      由 DOCX writer 转成原生 OMML 公式。
--   2. 样式表里的加粗/斜体：WPS/Excel 复制的 HTML 把样式写在 <style> 的
--      class 里，pandoc 不读样式表 → cli 解析 <style> 后把 class 名单经
--      环境变量传入，这里按 class 包装 Strong/Emph。
--   3. 首行全加粗的表格：<td> 加粗首行 pandoc 不认为是表头 → 提升为
--      TableHead（实验，默认关闭）。
-- 另外移除 .svg 位图引用（DOCX 无法嵌入，提前剔除避免误报图片丢失）。

local KATEX_DISPLAY = { ['katex-display'] = true }
local MATH_CLASSES = { ['math-inline'] = true, ['math-block'] = true, ['math-display'] = true }
local MATH_DISPLAY_CLASSES = { ['math-block'] = true, ['math-display'] = true }

local function load_class_list(value, target)
  if value == nil or value == '' then
    return
  end
  for name in string.gmatch(value, '[^,%s]+') do
    target[name] = true
  end
end

-- 注意：pandoc 3.x 过滤器里拿不到 PANDOC_DOCUMENT，Meta 处理器又晚于
-- 元素遍历执行，因此配置经环境变量传入，在首次使用时读取。
local config = nil

local function get_config()
  if config == nil then
    config = { bold = {}, italic = {}, promote = false }
    load_class_list(os.getenv('PASTEMD_FONT_BOLD_CLASSES'), config.bold)
    load_class_list(os.getenv('PASTEMD_FONT_ITALIC_CLASSES'), config.italic)
    local promote = os.getenv('PASTEMD_PROMOTE_BOLD_HEADER')
    if promote == 'true' or promote == '1' then
      config.promote = true
    end
  end
  return config
end

local function has_class(classes, set)
  for _, name in ipairs(classes or {}) do
    if set[name] then
      return true
    end
  end
  return false
end

local function elem_classes(elem)
  if type(elem.classes) == 'table' then
    return elem.classes
  end
  if type(elem.attr) == 'table' and type(elem.attr.classes) == 'table' then
    return elem.attr.classes
  end
  return {}
end

local function source_attr(attrs)
  return attrs['math-source'] or attrs['data-math-source']
end

local function latex_math(source, display)
  return pandoc.Math(display and 'DisplayMath' or 'InlineMath', source)
end

local function strip_delimiters(text)
  text = text:gsub('^%s+', ''):gsub('%s+$', '')
  text = text:gsub('^\\%(%s*', ''):gsub('%s*\\%)$', '')
  text = text:gsub('^\\%[%s*', ''):gsub('%s*\\%]$', '')
  return text:gsub('^%s+', ''):gsub('%s+$', '')
end

local function inline_has_display(inlines)
  for _, item in ipairs(inlines or {}) do
    if item.t == 'Span' then
      if has_class(elem_classes(item), KATEX_DISPLAY) then
        return true
      end
      if inline_has_display(item.content) then
        return true
      end
    end
  end
  return false
end

local function blocks_have_display(blocks)
  for _, block in ipairs(blocks or {}) do
    if block.t == 'Div' then
      if has_class(elem_classes(block), KATEX_DISPLAY) or blocks_have_display(block.content) then
        return true
      end
    elseif block.content and inline_has_display(block.content) then
      return true
    end
  end
  return false
end

local function restore_span_math(span)
  local source = source_attr(span.attributes or {})
  if source and source ~= '' then
    return latex_math(source, inline_has_display(span.content))
  end
  local copy = span.attributes and span.attributes['copy-text']
  if copy and copy ~= '' and has_class(span.classes, MATH_CLASSES) then
    local text = strip_delimiters(copy)
    if text ~= '' then
      return latex_math(text, has_class(span.classes, MATH_DISPLAY_CLASSES))
    end
  end
  return nil
end

local function wrap_inline(content, bold, italic)
  if bold and italic then
    return { pandoc.Strong{ pandoc.Emph(content) } }
  end
  if bold then
    return { pandoc.Strong(content) }
  end
  return { pandoc.Emph(content) }
end

local function cell_is_fully_bold(cell)
  local blocks = cell.content or {}
  if #blocks == 0 then
    return false
  end
  for _, block in ipairs(blocks) do
    if block.t ~= 'Plain' and block.t ~= 'Para' then
      return false
    end
    if #block.content == 0 then
      return false
    end
    for _, inline in ipairs(block.content) do
      if inline.t ~= 'Strong' then
        return false
      end
    end
  end
  return true
end

local function unbold_cell(cell)
  for _, block in ipairs(cell.content) do
    local plain = {}
    for _, inline in ipairs(block.content) do
      for _, inner in ipairs(inline.content or {}) do
        plain[#plain + 1] = inner
      end
    end
    block.content = plain
  end
  return cell
end

local function decorate_cells(cells)
  local cfg = get_config()
  for _, cell in ipairs(cells) do
    local bold = has_class(elem_classes(cell), cfg.bold)
    local italic = has_class(elem_classes(cell), cfg.italic)
    if bold or italic then
      for _, block in ipairs(cell.content or {}) do
        if block.t == 'Plain' or block.t == 'Para' then
          block.content = wrap_inline(block.content, bold, italic)
        end
      end
    end
  end
end

function Span(span)
  local cfg = get_config()
  local math = restore_span_math(span)
  if math ~= nil then
    return math
  end
  local bold = has_class(span.classes, cfg.bold)
  local italic = has_class(span.classes, cfg.italic)
  if not (bold or italic) then
    return nil
  end
  return wrap_inline(span.content, bold, italic)
end

function Div(div)
  local cfg = get_config()
  local source = source_attr(div.attributes or {})
  if source and source ~= '' then
    return { pandoc.Para{ latex_math(source, blocks_have_display(div.content)) } }
  end
  local bold = has_class(div.classes, cfg.bold)
  local italic = has_class(div.classes, cfg.italic)
  if not (bold or italic) then
    return nil
  end
  for _, block in ipairs(div.content or {}) do
    if block.t == 'Plain' or block.t == 'Para' then
      block.content = wrap_inline(block.content, bold, italic)
    end
  end
  return div
end

function Image(image)
  local source = (image.src or ''):lower():gsub('%?.*$', ''):gsub('#.*$', '')
  if source:match('%.svg$') then
    return {}
  end
  return nil
end

function Table(table)
  local cfg = get_config()
  if cfg.promote and #table.head.rows == 0 then
    for _, body in ipairs(table.bodies) do
      local rows = body.body or {}
      if #rows >= 2 and #rows[1].cells > 0 then
        local all_bold = true
        for _, cell in ipairs(rows[1].cells) do
          if not cell_is_fully_bold(cell) then
            all_bold = false
            break
          end
        end
        if all_bold then
          local head_row = rows[1]
          for _, cell in ipairs(head_row.cells) do
            unbold_cell(cell)
          end
          table.head.rows:insert(head_row)
          local remaining = pandoc.List{}
          for index = 2, #rows do
            remaining:insert(rows[index])
          end
          body.body = remaining
          return table
        end
      end
      break  -- 只考虑第一个表体，与上游一致
    end
  end
  if next(cfg.bold) ~= nil or next(cfg.italic) ~= nil then
    for _, head_row in ipairs(table.head.rows) do
      decorate_cells(head_row.cells)
    end
    for _, body in ipairs(table.bodies) do
      for _, row in ipairs(body.row_head or {}) do
        decorate_cells(row.cells)
      end
      for _, row in ipairs(body.body or {}) do
        decorate_cells(row.cells)
      end
    end
  end
  -- 就地修改必须显式返回元素，返回 nil 时 pandoc 使用原始副本
  return table
end
