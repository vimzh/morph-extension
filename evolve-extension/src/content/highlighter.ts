/**
 * Hover highlighter content script logic:
 * - Injects a fixed-position overlay and label for inspect-style highlighting.
 * - Tracks the element under the cursor and updates overlay bounds on move/scroll/resize.
 * - Uses background as source of truth for clicked elements.
 * - Keeps a pinned overlay on the last clicked element to ensure visibility.
 * - Ignores extension UI and only highlights elements on the host page.
 */
type ElementInfo = {
  tag: string
  id: string | null
  classes: string[]
}

type HighlighterState = {
  enabled: boolean
  moveCount: number
  lastElement: ElementInfo | null
}

const STORAGE_KEY = 'hoverHighlighter'
const STYLE_ID = 'evolve-hover-highlight-style'
const OVERLAY_ID = 'evolve-hover-highlight-overlay'
const LABEL_ID = 'evolve-hover-highlight-label'
const CLICK_OVERLAY_ID = 'evolve-click-highlight-overlay'
const CLICK_LABEL_ID = 'evolve-click-highlight-label'
const CLICKED_CLASS = 'evolve-clicked-highlight'
const SAVE_DEBOUNCE_MS = 250

import {
  MESSAGE_TYPES,
  type ClickedElementPayload,
  type ClickedElementStored,
} from '../shared/messages'


let state: HighlighterState = {
  enabled: true,
  moveCount: 0,
  lastElement: null,
}

let currentEl: Element | null = null
let pinnedEl: Element | null = null
let pinnedSelector: string | null = null
let saveTimer: number | undefined
let overlayEl: HTMLDivElement | null = null
let labelEl: HTMLDivElement | null = null
let clickOverlayEl: HTMLDivElement | null = null
let clickLabelEl: HTMLDivElement | null = null
let isMetaKeyDown = false
let isSidepanelOpen = false

/**
 * Injects the overlay/label and clicked-highlight styles once per page.
 */
function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
    #${OVERLAY_ID} {
      position: fixed;
      z-index: 2147483647 !important;
      border: 2px solid #ff6b00 !important;
      box-shadow: 0 0 0 1px #ff6b00, 0 0 0 2px rgba(255, 107, 0, 0.35) !important;
      background: transparent;
      box-sizing: border-box;
      pointer-events: none !important;
      display: none;
      mix-blend-mode: normal;
    }

    #${LABEL_ID} {
      position: fixed;
      z-index: 2147483647 !important;
      background: #ff6b00 !important;
      color: #111 !important;
      font: 11px/1.2 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      border-radius: 4px !important;
      padding: 2px 6px !important;
      pointer-events: none !important;
      display: none;
    }

    #${CLICK_OVERLAY_ID} {
      position: fixed;
      z-index: 2147483647 !important;
      border: 2px solid #ff6b00 !important;
      box-shadow: 0 0 0 1px #ff6b00, 0 0 0 2px rgba(255, 107, 0, 0.35) !important;
      background: transparent;
      box-sizing: border-box;
      pointer-events: none !important;
      display: none;
      mix-blend-mode: normal;
    }

    #${CLICK_LABEL_ID} {
      position: fixed;
      z-index: 2147483647 !important;
      background: #ff6b00 !important;
      color: #111 !important;
      font: 11px/1.2 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      border-radius: 4px !important;
      padding: 2px 6px !important;
      pointer-events: none !important;
      display: none;
    }

    .${CLICKED_CLASS} {
      outline: 2px solid #ff6b00 !important;
      outline-offset: 2px !important;
      background-color: rgba(255, 107, 0, 0.12) !important;
      position: relative !important;
      z-index: 2147483646 !important;
    }
  `
  document.head.appendChild(style)
}

/**
 * Ensures the overlay and label elements exist in the document.
 */
function ensureOverlay() {
  if (!overlayEl) {
    overlayEl = document.createElement('div')
    overlayEl.id = OVERLAY_ID
    document.documentElement.appendChild(overlayEl)
  }

  if (!labelEl) {
    labelEl = document.createElement('div')
    labelEl.id = LABEL_ID
    document.documentElement.appendChild(labelEl)
  }

  if (!clickOverlayEl) {
    clickOverlayEl = document.createElement('div')
    clickOverlayEl.id = CLICK_OVERLAY_ID
    document.documentElement.appendChild(clickOverlayEl)
  }

  if (!clickLabelEl) {
    clickLabelEl = document.createElement('div')
    clickLabelEl.id = CLICK_LABEL_ID
    document.documentElement.appendChild(clickLabelEl)
  }
}

/**
 * Extracts basic metadata from an element for storage.
 */
function getElementInfo(el: Element): ElementInfo {
  return {
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    classes: [...el.classList],
  }
}

/**
 * Returns true if the element belongs to the extension UI container.
 */
function isExtensionUi(el: Element): boolean {
  return Boolean(el.closest('#evolve-app'))
}

/**
 * Formats a short label for the hover overlay.
 */
function getElementLabel(el: Element): string {
  const tag = el.tagName.toLowerCase()
  const id = el.id ? `#${el.id}` : ''
  const firstClass = el.classList.length > 0 ? `.${el.classList[0]}` : ''
  return `${tag}${id}${firstClass}`
}

/**
 * Builds a deterministic selector to re-find the element later.
 */
function getElementSelector(el: Element): string | null {
  if (el.id) return `#${CSS.escape(el.id)}`

  const parts: string[] = []
  let current: Element | null = el
  while (current && current !== document.documentElement) {
    const tag = current.tagName.toLowerCase()
    const parent: Element | null = current.parentElement
    if (!parent) break
    const siblings = (Array.from(parent.children) as Element[]).filter(
      (child) => child.tagName.toLowerCase() === tag,
    )
    const index = siblings.indexOf(current) + 1
    parts.unshift(`${tag}:nth-of-type(${index})`)
    current = parent
  }

  return parts.length ? parts.join(' > ') : null
}

/**
 * Persists the current hover state with a debounce.
 */
function scheduleSave() {
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    chrome.storage.local.set({ [STORAGE_KEY]: state })
  }, SAVE_DEBOUNCE_MS)
}

/**
 * Stores a clicked element (tied to the active tab) via the background worker.
 */
async function toggleClickedElement(el: Element) {
  const selector = getElementSelector(el)
  if (!selector) return

  const payload: ClickedElementPayload = {
    ...getElementInfo(el),
    selector,
    url: window.location.href,
    timestamp: Date.now(),
  }

  const response = await chrome.runtime.sendMessage({
    type: MESSAGE_TYPES.toggleClicked,
    payload,
  })

  if (response?.action === 'removed') {
    el.classList.remove(CLICKED_CLASS)
    if (pinnedSelector === selector) {
      hideClickedOverlay()
    }
    return
  }

  el.classList.add(CLICKED_CLASS)
  pinnedSelector = selector
  applyClickedOverlay(el)
}

/**
 * Restores persistent clicked highlights for the current URL/tab.
 */
async function restoreClickedHighlights() {
  const response = await chrome.runtime.sendMessage({
    type: MESSAGE_TYPES.getClicked,
    url: window.location.href,
  })
  const list = response?.elements as ClickedElementStored[] | undefined
  if (!Array.isArray(list)) return

  const selectors = new Set(list.map((item) => item?.selector).filter(Boolean) as string[])

  const highlighted = document.querySelectorAll(`.${CLICKED_CLASS}`)
  highlighted.forEach((el) => {
    if (!(el instanceof Element)) return
    const selector = getElementSelector(el)
    if (!selector || !selectors.has(selector)) {
      el.classList.remove(CLICKED_CLASS)
    }
  })

  for (const item of list) {
    const selector = item?.selector
    if (typeof selector !== 'string') continue
    const el = document.querySelector(selector)
    if (el) {
      el.classList.add(CLICKED_CLASS)
    }
  }
}

/**
 * Removes clicked highlight classes from the DOM without clearing storage.
 */
function hideClickedHighlights() {
  const highlighted = document.querySelectorAll(`.${CLICKED_CLASS}`)
  highlighted.forEach((el) => el.classList.remove(CLICKED_CLASS))
}

async function clearAllClickedHighlights() {
  hideClickedHighlights()

  await chrome.runtime.sendMessage({
    type: MESSAGE_TYPES.clearAllClicked,
  })
}

async function syncSidepanelState() {
  try {
    const response = await chrome.runtime.sendMessage({
      type: MESSAGE_TYPES.getSidepanelState,
    })
    setSidepanelState(Boolean(response?.isOpen))
  } catch {
    // Ignore errors to keep highlighter functional
  }
}


/**
 * Hides the hover overlay and label.
 */
function hideOverlay() {
  if (overlayEl) overlayEl.style.display = 'none'
  if (labelEl) labelEl.style.display = 'none'
}

function hideClickedOverlay() {
  if (clickOverlayEl) clickOverlayEl.style.display = 'none'
  if (clickLabelEl) clickLabelEl.style.display = 'none'
  pinnedEl = null
  pinnedSelector = null
}

/**
 * Positions the overlay and label around the current element.
 */
function applyHighlight(el: Element | null) {
  ensureOverlay()

  if (!overlayEl || !labelEl) return
  if (!el) {
    currentEl = null
    hideOverlay()
    return
  }

  currentEl = el

  const rect = el.getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) {
    hideOverlay()
    return
  }

  overlayEl.style.display = 'block'
  overlayEl.style.left = `${rect.left}px`
  overlayEl.style.top = `${rect.top}px`
  overlayEl.style.width = `${rect.width}px`
  overlayEl.style.height = `${rect.height}px`

  labelEl.textContent = getElementLabel(el)
  labelEl.style.display = 'block'
  labelEl.style.left = `${Math.max(4, rect.left)}px`
  labelEl.style.top = `${Math.max(4, rect.top - 22)}px`
}

function applyClickedOverlay(el: Element | null) {
  ensureOverlay()

  if (!clickOverlayEl || !clickLabelEl) return
  if (!el || !document.contains(el)) {
    hideClickedOverlay()
    return
  }

  pinnedEl = el

  const rect = el.getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) {
    hideClickedOverlay()
    return
  }

  clickOverlayEl.style.display = 'block'
  clickOverlayEl.style.left = `${rect.left}px`
  clickOverlayEl.style.top = `${rect.top}px`
  clickOverlayEl.style.width = `${rect.width}px`
  clickOverlayEl.style.height = `${rect.height}px`

  clickLabelEl.textContent = getElementLabel(el)
  clickLabelEl.style.display = 'block'
  clickLabelEl.style.left = `${Math.max(4, rect.left)}px`
  clickLabelEl.style.top = `${Math.max(4, rect.top - 22)}px`
}

/**
 * Handles hover tracking while Cmd is held.
 */
function handleMouseMove(event: MouseEvent) {
  if (!state.enabled) return
  if (!isSidepanelOpen) return
  if (!isMetaKeyDown) return
  const el = document.elementFromPoint(event.clientX, event.clientY)
  if (!el || isExtensionUi(el)) return

  if (el === currentEl) return

  applyHighlight(el)
  state = {
    ...state,
    moveCount: state.moveCount + 1,
    lastElement: getElementInfo(el),
  }
  scheduleSave()
}

function setSidepanelState(open: boolean) {
  if (isSidepanelOpen === open) return
  isSidepanelOpen = open

  if (isSidepanelOpen) {
    void restoreClickedHighlights()
    return
  }

  applyHighlight(null)
  hideClickedHighlights()
  hideClickedOverlay()
}

/**
 * Initializes overlay, restores highlights, and wires event listeners.
 */
export async function startHoverHighlighter() {
  ensureStyle()
  ensureOverlay()

  await syncSidepanelState()

  try {
    const stored = await chrome.storage.local.get(STORAGE_KEY)
    const saved = stored[STORAGE_KEY] as HighlighterState | undefined
    if (saved) {
      state = {
        enabled: state.enabled,
        moveCount: typeof saved.moveCount === 'number' ? saved.moveCount : 0,
        lastElement: saved.lastElement ?? null,
      }
    }
  } catch {
    // Ignore storage errors to keep highlighter functional
  }

  try {
    if (isSidepanelOpen) {
      await restoreClickedHighlights()
    }
  } catch {
    // Ignore storage errors to keep highlighter functional
  }

  window.addEventListener('mousemove', handleMouseMove, { passive: true })
  window.addEventListener('scroll', () => {
    applyHighlight(currentEl)
    applyClickedOverlay(pinnedEl)
  }, { passive: true })
  window.addEventListener('resize', () => {
    applyHighlight(currentEl)
    applyClickedOverlay(pinnedEl)
  })

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      applyHighlight(null)
      isMetaKeyDown = false
      void clearAllClickedHighlights()
      return
    }

    if (event.metaKey) {
      isMetaKeyDown = true
    }
  }, true)

  document.addEventListener('keyup', (event) => {
    if (event.key === 'Meta' || !event.metaKey) {
      if (isMetaKeyDown) {
        isMetaKeyDown = false
        applyHighlight(null)
      }
    }
  }, true)

  window.addEventListener('blur', () => {
    if (isMetaKeyDown) {
      isMetaKeyDown = false
      applyHighlight(null)
    }
  })

  document.addEventListener('click', (event) => {
    if (!isSidepanelOpen) return
    if (!isMetaKeyDown) return
    event.preventDefault()
    event.stopImmediatePropagation()
    const target = event.target
    if (!(target instanceof Element)) return
    if (isExtensionUi(target)) return

    void toggleClickedElement(target)
  }, true)

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === MESSAGE_TYPES.sidepanelOpen) {
      setSidepanelState(true)
      return
    }

    if (message?.type === MESSAGE_TYPES.sidepanelClose) {
      setSidepanelState(false)
      return
    }

    if (message?.type === MESSAGE_TYPES.unhighlightSelector) {
      const selector = message?.selector
      if (typeof selector !== 'string') return
      const el = document.querySelector(selector)
      if (el) {
        el.classList.remove(CLICKED_CLASS)
      }
      if (pinnedSelector === selector) {
        hideClickedOverlay()
      }
      return
    }

    if (message?.type === MESSAGE_TYPES.highlightSelector) {
      const selector = message?.selector
      if (typeof selector !== 'string') return
      const el = document.querySelector(selector)
      if (el) {
        el.classList.add(CLICKED_CLASS)
      }
      return
    }

    if (message?.type === MESSAGE_TYPES.clearHighlightsDom) {
      hideClickedHighlights()
      hideClickedOverlay()
    }

    if (message?.type === MESSAGE_TYPES.clickedElementsUpdated) {
      if (!isSidepanelOpen) return
      void restoreClickedHighlights()
    }
  })
}

/**
 * Enables or disables hover highlighting and persists the state.
 */
export function setHoverHighlighterEnabled(enabled: boolean) {
  if (state.enabled === enabled) return
  state = { ...state, enabled }
  if (!enabled) {
    applyHighlight(null)
  }
  scheduleSave()
}
