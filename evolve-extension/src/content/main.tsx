import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { setHoverHighlighterEnabled, startHoverHighlighter } from './highlighter'
import { MESSAGE_TYPES } from '../shared/messages'
import App from './views/App.tsx'

console.log('[Evolve] Hello world from content script!')

startHoverHighlighter()

const sanitizeElementTree = (root: Element) => {
  const selectors = [
    'script',
    'style',
    'noscript',
    'svg',
    'header',
    'nav',
    'footer',
    'iframe',
    'canvas',
    'template',
  ]

  selectors.forEach((selector) => {
    root.querySelectorAll(selector).forEach((el) => el.remove())
  })

  root.querySelectorAll('[hidden], [aria-hidden="true"]').forEach((el) => el.remove())

  root.querySelectorAll<HTMLElement>('[style]').forEach((el) => {
    const style = el.getAttribute('style')?.toLowerCase() || ''
    if (style.includes('display:none') || style.includes('visibility:hidden')) {
      el.remove()
    }
  })
}

// Listen for sidepanel lifecycle messages to toggle the highlighter
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === MESSAGE_TYPES.sidepanelOpen) {
    setHoverHighlighterEnabled(true)
    return
  }

  if (message?.type === MESSAGE_TYPES.sidepanelClose) {
    setHoverHighlighterEnabled(false)
    return
  }

  if (message?.type === MESSAGE_TYPES.getPageContent) {
    // Return full page content — raw HTML or visible text depending on the flag.
    // Pagination / chunking is handled server-side.
    const raw = message.includeHtml
      ? (document.body?.innerHTML ?? '')
      : (document.body?.innerText ?? '')
    sendResponse({ content: raw })
    return // synchronous response
  }

  if (message?.type === MESSAGE_TYPES.getElementHtml) {
    const selector = message?.selector
    const stripHighlighting = (root: Element) => {
      const idsToRemove = [
        'evolve-hover-highlight-style',
        'evolve-hover-highlight-overlay',
        'evolve-hover-highlight-label',
        'evolve-click-highlight-overlay',
        'evolve-click-highlight-label',
      ]

      if (root.classList.contains('evolve-clicked-highlight')) {
        root.classList.remove('evolve-clicked-highlight')
      }

      root.querySelectorAll('.evolve-clicked-highlight').forEach((el) => {
        el.classList.remove('evolve-clicked-highlight')
      })

      idsToRemove.forEach((id) => {
        root.querySelectorAll(`#${id}`).forEach((el) => el.remove())
      })
    }
    if (typeof selector === 'string' && selector.length > 0) {
      const el = document.querySelector(selector)
      if (!el) {
        sendResponse({ html: '' })
        return true
      }
      const clone = el.cloneNode(true) as Element
      stripHighlighting(clone)
      sanitizeElementTree(clone)
      sendResponse({ html: clone.outerHTML })
      return true
    }
    const body = document.body?.cloneNode(true) as Element | null
    if (!body) {
      sendResponse({ html: '' })
      return true
    }
    stripHighlighting(body)
    sanitizeElementTree(body)
    sendResponse({ html: body.outerHTML })
    return true
  }
})

const container = document.createElement('div')
container.id = 'evolve-app'
document.body.appendChild(container)
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
