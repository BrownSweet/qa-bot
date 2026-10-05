import { marked } from 'marked'
import DOMPurify from 'dompurify'

export function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text || '', { breaks: true }), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'iframe', 'form', 'input', 'button', 'img', 'video', 'audio'],
    FORBID_ATTR: ['style'],
  })
}
