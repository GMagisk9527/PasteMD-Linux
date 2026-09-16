-- 配置表：将常用的正则模式映射为目标替换字符串
-- 优势：方便扩展，只需在 mappings 中添加新项即可
local mappings = {
  -- 匹配 \kern 或 {\kern} 后跟数值和单位 (pt, em, cm, mm, ex, bp)
  -- 模式解释：\\kern%s*%-?%d*%.?%d+%a%a
  { 
    pattern = "{\\kern%s*[^}]+}", 
    replacement = "\\qquad" 
  },
  { 
    pattern = "\\kern%s*%-?%d*%.?%d+%a%a", 
    replacement = "\\qquad" 
  },
  
  -- 示例：在此处添加更多扩展规则
  -- { pattern = "\\mbox%s*(%b{})", replacement = "\\text%1" }, 
}

--- 核心处理逻辑：遍历配置表进行文本替换
local function apply_replacements(content)
  for _, rule in ipairs(mappings) do
    content = content:gsub(rule.pattern, rule.replacement)
  end
  return content
end

--- Pandoc 过滤器入口
return {
  {
    Math = function(el)
      el.text = apply_replacements(el.text)
      return el
    end
  }
}