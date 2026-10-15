function emptyUsage() {
  return { claude: { calls: 0, input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0 }, chatgpt: { tokens: 0 } };
}

// JSON telemetry is optional: older CLI output and missing usage still produce answers.
function parseClaude(raw, onUsage) {
  let body;
  try { body = JSON.parse(raw); } catch {}
  if (typeof body?.result === 'string') {
    const usage = body.usage || {};
    onUsage?.({ claude: {
      calls: 1,
      input: Number(usage.input_tokens) || 0,
      output: Number(usage.output_tokens) || 0,
      cacheRead: Number(usage.cache_read_input_tokens) || 0,
      cacheWrite: Number(usage.cache_creation_input_tokens) || 0,
      costUsd: Number(body.total_cost_usd) || 0,
    } });
    return body.result;
  }
  onUsage?.({ claude: { calls: 1 } });
  return raw;
}
function codexTokens(raw) {
  return Number(String(raw).match(/tokens used\s*[:：]?\s*([\d,]+)/i)?.[1]?.replace(/,/g, '')) || 0;
}
function addUsage(total, part) {
  for (const name of ['claude', 'chatgpt']) {
    for (const key of Object.keys(total[name])) total[name][key] += Number(part?.[name]?.[key]) || 0;
  }
  return total;
}
function tokenTotals(records) {
  const total = emptyUsage();
  records.forEach(record => addUsage(total, record));
  return { claude: total.claude.input + total.claude.output + total.claude.cacheRead + total.claude.cacheWrite, chatgpt: total.chatgpt.tokens };
}
module.exports = { emptyUsage, parseClaude, codexTokens, addUsage, tokenTotals };
