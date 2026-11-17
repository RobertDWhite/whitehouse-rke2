// Riffado sends summaries with max_tokens 2000 and has no reasoning switch.
// Qwen3.6 spends that whole budget thinking and returns empty content, so
// turn thinking off unless the request already chose.
function body(r) {
  const text = r.requestText;
  if (r.method !== 'POST' || !text) {
    return text || '';
  }
  try {
    const req = JSON.parse(text);
    if (!Array.isArray(req.messages)) {
      return text;
    }
    req.chat_template_kwargs = req.chat_template_kwargs || {};
    if (req.chat_template_kwargs.enable_thinking === undefined) {
      req.chat_template_kwargs.enable_thinking = false;
    }
    return JSON.stringify(req);
  } catch (e) {
    return text;
  }
}

export default { body };
