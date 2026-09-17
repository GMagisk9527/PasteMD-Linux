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
    config = { bold = {}, italic = {}, promote = false, protect_tasks = false,
               prewrap = {}, prewrap_on = false }
    load_class_list(os.getenv('PASTEMD_FONT_BOLD_CLASSES'), config.bold)
    load_class_list(os.getenv('PASTEMD_FONT_ITALIC_CLASSES'), config.italic)
    load_class_list(os.getenv('PASTEMD_PREWRAP_CLASSES'), config.prewrap)
    local promote = os.getenv('PASTEMD_PROMOTE_BOLD_HEADER')
    if promote == 'true' or promote == '1' then
      config.promote = true
    end
    local protect = os.getenv('PASTEMD_PROTECT_TASKS')
    if protect == '1' or protect == 'true' then
      config.protect_tasks = true
    end
    local prewrap = os.getenv('PASTEMD_PRESERVE_PREWRAP')
    if prewrap == '1' or prewrap == 'true' then
      config.prewrap_on = true
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
  -- 部分来源会把反斜杠转义成双写（\\( … \\)），先归一化为单反斜杠
  text = text:gsub('\\+\\%(', '\\(')
  text = text:gsub('\\+\\%)', '\\)')
  text = text:gsub('\\+\\%[', '\\[')
  text = text:gsub('\\+\\%]', '\\]')
  text = text:gsub('^\\%(%s*', ''):gsub('%s*\\%)$', '')
  text = text:gsub('^\\%[%s*', ''):gsub('%s*\\%]$', '')
  -- $$…$$ 与成对的单 $（仅当两端同时出现才剥，避免误伤正文里的美元符号）
  if text:sub(1, 2) == '$$' and text:sub(-2) == '$$' and #text > 4 then
    text = text:sub(3, -3)
  elseif text:sub(1, 1) == '$' and text:sub(-1) == '$' and #text > 2 then
    text = text:sub(2, -2)
  end
  return text:gsub('^%s+', ''):gsub('%s+$', '')
end

local MATH_CLASSES = { ['math'] = true }

local function is_math_class(classes)
  return has_class(classes, MATH_CLASSES)
end

local function is_display_math_class(classes)
  return has_class(classes, { ['math-block'] = true, ['math-display'] = true })
end

local function collect_text(inlines)
  local parts = {}
  local function walk(nodes)
    for _, node in ipairs(nodes or {}) do
      if node.t == 'Str' then
        parts[#parts + 1] = node.text
      elseif node.t == 'Space' or node.t == 'SoftBreak' or node.t == 'LineBreak' then
        parts[#parts + 1] = ' '
      elseif type(node.content) == 'table' then
        walk(node.content)
      end
    end
  end
  walk(inlines)
  return table.concat(parts)
end

local function math_from_text(text, display)
  text = strip_delimiters(text)
  if text == '' then
    return nil
  end
  return pandoc.Math(display and 'DisplayMath' or 'InlineMath', text)
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

-- 任务列表保护：把 [x]/[ ] 换成占位符，避免 gfm writer 转义成 \[x]；
-- 由 cli 在转 Markdown 文本的目标上经环境变量开启，转换后再还原。
-- pandoc 的 HTML reader 会把 "[ ]" 拆成 Str("["), Space, Str("]")，
-- 因此除连续子串外还要按三件套匹配。
local function protect_inlines(inlines)
  local changed = false
  local index = 1
  while index <= #inlines do
    local a, b, c = inlines[index], inlines[index + 1], inlines[index + 2]
    if a and a.t == 'Str' and b and b.t == 'Space' and c and c.t == 'Str'
        and string.sub(a.text, -1) == '[' and string.sub(c.text, 1, 1) == ']' then
      local head = string.sub(a.text, 1, -2)
      local tail = string.sub(c.text, 2)
      inlines[index] = pandoc.Str(head .. '{{PASTEMD_TASK_UNCHECKED}}' .. tail)
      table.remove(inlines, index + 1)
      table.remove(inlines, index + 1)
      changed = true
    elseif a and a.t == 'Str' and string.find(a.text, '[', 1, true) then
      local replaced = a.text:gsub('%[[xX]%]', '{{PASTEMD_TASK_CHECKED}}')
      replaced = replaced:gsub('%[ %]', '{{PASTEMD_TASK_UNCHECKED}}')
      if replaced ~= a.text then
        inlines[index] = pandoc.Str(replaced)
        changed = true
      end
    end
    index = index + 1
  end
  return changed
end

-- white-space:pre-wrap 块里的源码换行被 pandoc 统一折叠成 SoftBreak，
-- 输出时渲染为空格；这里换回 LineBreak（docx 落 w:br，gfm 落硬换行）
local function prewrap_inlines(inlines)
  local changed = false
  for index = 1, #inlines do
    local inline = inlines[index]
    if inline.t == 'SoftBreak' then
      -- 元素属性只读（.t 是 read-only tag），必须整槽位替换
      inlines[index] = pandoc.LineBreak()
      changed = true
    else
      local ok, content = pcall(function() return inline.content end)
      if ok and content ~= nil and prewrap_inlines(content) then
        changed = true
      end
    end
  end
  return changed
end

local function prewrap_blocks(blocks)
  local changed = false
  for _, block in ipairs(blocks or {}) do
    if (block.t == 'Plain' or block.t == 'Para') and prewrap_inlines(block.content) then
      changed = true
    end
  end
  return changed
end

local function style_value(elem)
  -- pandoc 3.7 的 attr 是 userdata，type() 检查不可靠，用 pcall 直接索引
  local ok, style = pcall(function() return elem.attributes['style'] end)
  if not ok or not style or style == '' then
    ok, style = pcall(function() return elem.attr.attributes['style'] end)
  end
  if ok and style then
    return tostring(style)
  end
  return ''
end

local function is_prewrap(elem, cfg)
  if style_value(elem):find('pre%-wrap', 1, false) then
    return true
  end
  local ok, classes = pcall(function() return elem.classes end)
  if ok then
    return has_class(classes, cfg.prewrap)
  end
  return false
end

function Para(para)
  if get_config().protect_tasks then
    protect_inlines(para.content)
    return para
  end
  return nil
end

function Plain(plain)
  if get_config().protect_tasks then
    protect_inlines(plain.content)
    return plain
  end
  return nil
end

function Span(span)
  local cfg = get_config()
  local math = restore_span_math(span)
  if math ~= nil then
    return math
  end
  -- Obsidian 等编辑器把公式包成 <span class="math math-inline|math-block">
  if is_math_class(span.classes) then
    local math_elem = math_from_text(collect_text(span.content),
                                     is_display_math_class(span.classes))
    if math_elem ~= nil then
      return math_elem
    end
  end
  -- 就地修改必须显式返回元素，返回 nil 时 pandoc 使用原始副本
  local changed = false
  if cfg.protect_tasks then
    changed = protect_inlines(span.content) or changed
  end
  if cfg.prewrap_on and is_prewrap(span, cfg) then
    changed = prewrap_inlines(span.content) or changed
  end
  local bold = has_class(span.classes, cfg.bold)
  local italic = has_class(span.classes, cfg.italic)
  if bold or italic then
    span.content = wrap_inline(span.content, bold, italic)
    return span
  end
  if changed then
    return span
  end
  return nil
end

function Div(div)
  local cfg = get_config()
  local source = source_attr(div.attributes or {})
  if source and source ~= '' then
    return { pandoc.Para{ latex_math(source, blocks_have_display(div.content)) } }
  end
  -- Obsidian 块级公式：<div class="math math-block">…</div>
  if is_math_class(div.classes) then
    local parts = {}
    for _, block in ipairs(div.content or {}) do
      if block.t == 'Plain' or block.t == 'Para' then
        parts[#parts + 1] = collect_text(block.content)
      end
    end
    local math_elem = math_from_text(table.concat(parts, ' '), true)
    if math_elem ~= nil then
      return { pandoc.Plain{ math_elem } }
    end
  end
  local prewrap_changed = false
  if cfg.prewrap_on and is_prewrap(div, cfg) then
    prewrap_changed = prewrap_blocks(div.content)
  end
  local bold = has_class(div.classes, cfg.bold)
  local italic = has_class(div.classes, cfg.italic)
  if bold or italic then
    for _, block in ipairs(div.content or {}) do
      if block.t == 'Plain' or block.t == 'Para' then
        block.content = wrap_inline(block.content, bold, italic)
      end
    end
    return div
  end
  if prewrap_changed then
    return div
  end
  return nil
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
