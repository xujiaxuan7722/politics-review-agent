import MarkdownIt from "markdown-it";

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
});

export function renderMarkdown(text = "") {
  return md.render(String(text || ""));
}
