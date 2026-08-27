import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const readJSON = (file) => JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
const flatten = (value, prefix = '') => Object.entries(value).flatMap(([key, child]) => {
  const name = prefix ? `${prefix}.${key}` : key;
  return child && typeof child === 'object' && !Array.isArray(child) ? flatten(child, name) : [name];
});
const english = new Set(flatten(readJSON('i18n/messages/en.json')));
const chinese = new Set(flatten(readJSON('i18n/messages/zh.json')));
const missingChinese = [...english].filter((key) => !chinese.has(key));
const missingEnglish = [...chinese].filter((key) => !english.has(key));
if (missingChinese.length || missingEnglish.length) {
  throw new Error(`Translation keys differ. en-only=${missingChinese.join(',')} zh-only=${missingEnglish.join(',')}`);
}

const roots = ['app', 'components'];
const files = [];
const visit = (directory) => {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) visit(target);
    else if (entry.name.endsWith('.tsx')) files.push(target);
  }
};
roots.forEach((directory) => visit(path.join(root, directory)));
const allowed = /^(?:Lode|OpenAI|Anthropic|API Key|OpenAI Responses|OpenAI Chat Completions|Anthropic Messages)$/;
const findings = [];
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  for (const match of source.matchAll(/>([A-Za-z][^<{\n]*)</g)) {
    const text = match[1].trim();
    if (text && !allowed.test(text)) findings.push(`${path.relative(root, file)}: ${text}`);
  }
  for (const match of source.matchAll(/(?:aria-label|title|placeholder)="([A-Za-z][^"]*)"/g)) {
    const text = match[1].trim();
    if (text && !allowed.test(text) && !text.startsWith('http') && !text.startsWith('/')) {
      findings.push(`${path.relative(root, file)}: ${text}`);
    }
  }
}
if (findings.length) throw new Error(`Untranslated visible strings:\n${findings.join('\n')}`);
console.log(`i18n check passed (${english.size} keys, ${files.length} TSX files)`);
