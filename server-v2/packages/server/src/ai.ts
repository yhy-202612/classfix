import { config, db } from './store';
import { parseJsonLoose, requestJson } from './util';

/* 提示词原文与旧版逐字一致：改字会改变模型输出风格，属于行为变更。 */

const DIAGNOSE_RULES = [
  '你是 ClassFix 的课堂设备急救顾问。只允许引用输入里给出的检测数据，缺失的信息要说「尚未检测」，不得臆造。',
  '输出严格 JSON，不要 Markdown 代码块之外的任何文字：',
  '{"advice":"给老师看的建议，200 字内，先给最快可逆的恢复步骤","steps":["可执行步骤"],"verify":"怎么验证已恢复",',
  ' "issues":[{"title":"问题名，12 字内","checkKey":"最相关的检测项 key，没有就空字符串","evidence":"依据","action":"处置"}]}',
  'issues 只列「检测数据里确实存在、但常规修复流程没覆盖」的问题；没有就给空数组。',
];

/** 诊断系统提示词：把「离线已覆盖」的基线拼进去，避免模型重复提已知问题。 */
export function diagnosePrompt(): string {
  const lines = DIAGNOSE_RULES.slice();
  const baseline = (db.catalog || [])
    .map((item) => String(item.title || '').trim())
    .filter(Boolean)
    .slice(0, 120);
  if (baseline.length) {
    lines.push('');
    lines.push(
      '下面这些问题离线修复流程已经会给处置建议，不要把它们当成「未覆盖」写进 issues（除非本次数据的成因明显与已有处置不同）：',
    );
    lines.push(baseline.join('、'));
  }
  return lines.join('\n');
}

export const SOLUTION_PROMPT = [
  '你是 ClassFix 的知识整理员。下面会给你若干条针对同一类课堂设备问题的诊断片段。',
  '请把它们归纳成一条可复用的修复方案，输出严格 JSON：',
  '{"title":"方案名，16 字内","appliesTo":["最相关的检测项 key"],"steps":["现场可执行的步骤"],',
  ' "verify":"验证方法","confidence":"high|medium|low"}',
  '步骤必须能在一间教室里当场做完，不许要求上传文件、安装驱动或改注册表。',
].join('\n');

export function aiConfig(): { url: string; model: string; key: string; timeout: number } {
  const base = String(config.aiBase || 'https://api.deepseek.com/v1').replace(/\/+$/, '');
  return {
    url: base + '/chat/completions',
    model: config.aiModel || 'deepseek-flash',
    key: config.aiKey || '',
    timeout: Number(config.aiTimeout || 45000),
  };
}

export type AskResult = { text: string; parsed: any | null };

/** 调用上游模型（服务端持密钥）。失败抛错，由调用方决定是否退款。 */
export async function askModel(systemPrompt: string, userContent: string, maxTokens?: number): Promise<AskResult> {
  const ai = aiConfig();
  if (!ai.key) throw new Error('服务端未配置 AI 密钥');

  const response = await requestJson(
    { url: ai.url, timeout: ai.timeout, headers: { Authorization: 'Bearer ' + ai.key } },
    {
      model: ai.model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userContent },
      ],
      max_tokens: maxTokens || 1600,
      temperature: 0.3,
      stream: false,
    },
  );

  if (response.status !== 200 || !response.json) {
    const reason = (response.json && response.json.error && response.json.error.message) || 'HTTP ' + response.status;
    throw new Error('AI 服务返回异常：' + reason);
  }

  const content =
    response.json.choices && response.json.choices[0] && response.json.choices[0].message
      ? response.json.choices[0].message.content
      : null;
  if (!content) throw new Error('AI 没有返回内容');

  return { text: String(content), parsed: parseJsonLoose(String(content)) };
}
