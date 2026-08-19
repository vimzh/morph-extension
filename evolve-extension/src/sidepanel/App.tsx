import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  MESSAGE_TYPES,
  type ClickedElementStored,
} from '../shared/messages'
import './App.css'

const API_URL = 'http://localhost:8000'
const WS_URL = API_URL.replace(/^http/, 'ws')

interface Project {
  id: string
  name: string
  created_at: string
}

interface Conversation {
  id: string
  title?: string
  created_at: string
}

type TabMatch = {
  id: number
  title: string
  url: string
}

type MessagePart =
  | { type: 'text'; content: string }
  | { type: 'tool'; name: string; label: string; status: 'running' | 'done'; favIconUrl?: string }

type DisplayPart =
  | { type: 'text'; content: string }
  | { type: 'chip'; items: ClickedElementStored[] }

interface Message {
  role: string
  content: string
  created_at: string
  parts?: MessagePart[]
  displayParts?: DisplayPart[]
}

type SidebarView = 'projects' | 'conversations'

const TOOL_LABELS: Record<string, string> = {
  list_dir: 'List directory',
  read_file: 'Read',
  grep_search: 'Search',
  create_file: 'Create',
  edit_file: 'Edit',
  run_terminal_command: 'Run',
  get_tab_content: 'Read tab',
}

type TabInfo = { id: number; url: string; title: string; favIconUrl?: string }

function getToolLabel(
  name: string,
  args: Record<string, unknown>,
  tabsMap?: Map<number, TabInfo>,
): { label: string; favIconUrl?: string } {
  const prefix = TOOL_LABELS[name] || name
  switch (name) {
    case 'list_dir':
      return { label: `${prefix} ${args.relative_workspace_path || '.'}` }
    case 'read_file':
      return { label: `${prefix} ${args.target_file || ''}` }
    case 'grep_search':
      return { label: `${prefix} "${args.query || ''}"` }
    case 'create_file':
      return { label: `${prefix} ${args.target_file || ''}` }
    case 'edit_file':
      return { label: `${prefix} ${args.target_file || ''}` }
    case 'run_terminal_command': {
      const cmd = String(args.command || '')
      return { label: `${prefix} ${cmd.length > 30 ? cmd.slice(0, 30) + '...' : cmd}` }
    }
    case 'get_tab_content': {
      const tabId = Number(args.tab_id)
      const tab = tabsMap?.get(tabId)
      if (tab) {
        const title = tab.title.length > 30 ? tab.title.slice(0, 30) + '...' : tab.title
        return { label: `${prefix} ${title}`, favIconUrl: tab.favIconUrl }
      }
      return { label: `${prefix} (tab ${args.tab_id || '?'})` }
    }
    default:
      return { label: prefix }
  }
}

// Sidepanel chat UI with inline, coalesced selection chips inside a contenteditable editor.
export default function App() {
  // Project state
  const [projects, setProjects] = useState<Project[]>([])
  const [activeProject, setActiveProject] = useState<Project | null>(null)
  const [newProjectName, setNewProjectName] = useState('')
  const [creatingProject, setCreatingProject] = useState(false)

  // Conversation state
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // UI state
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarView, setSidebarView] = useState<SidebarView>('projects')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const editorRef = useRef<HTMLDivElement>(null)
  const knownChipIdsRef = useRef<Set<string>>(new Set())
  const mentionRangeRef = useRef<{ start: number; end: number } | null>(null)
  const tabsCacheRef = useRef<TabMatch[]>([])
  const mentionTooltipRef = useRef<HTMLDivElement>(null)

  // WebSocket
  const wsRef = useRef<WebSocket | null>(null)
  // Cache of tab info for resolving tab IDs to titles/favicons in tool labels
  const tabsMapRef = useRef<Map<number, TabInfo>>(new Map())
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const messageHandlerRef = useRef<((data: Record<string, any>) => void) | null>(null)

  // Highlighted elements context
  const [clickedElements, setClickedElements] = useState<ClickedElementStored[]>([])
  const [mentionOpen, setMentionOpen] = useState(false)
  const [mentionPosition, setMentionPosition] = useState<{ top: number; left: number } | null>(null)
  const [tabMatches, setTabMatches] = useState<TabMatch[]>([])
  const [mentionSelectedIndex, setMentionSelectedIndex] = useState(0)

  // Sidepanel open/close state is derived from chrome.sidePanel.onStateChanged in the background.

  // Load projects on mount
  useEffect(() => {
    fetchProjects()
  }, [])

  const formatElementShortLabel = (item: ClickedElementStored) => {
    if (item.tag === 'tab') {
      return item.tabTitle || getHost(item)
    }
    const tag = item.tag
    const id = item.id ? `#${item.id}` : ''
    const firstClass = item.classes?.length ? `.${item.classes[0]}` : ''
    return `${tag}${id}${firstClass}`
  }

  const getHost = (item: ClickedElementStored) => {
    try {
      return new URL(item.url).host
    } catch {
      return 'unknown'
    }
  }

  const getHostFromUrl = (url: string) => {
    try {
      return new URL(url).host
    } catch {
      return 'unknown'
    }
  }

  const getFaviconUrl = (host: string) => `https://www.google.com/s2/favicons?domain=${host}&sz=16`

  const waitForSocketOpen = (ws: WebSocket, timeoutMs = 5000) =>
    new Promise<boolean>((resolve) => {
      if (ws.readyState === WebSocket.OPEN) {
        resolve(true)
        return
      }
      if (ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED) {
        resolve(false)
        return
      }

      let settled = false
      const timer = setTimeout(() => {
        cleanup()
        resolve(false)
      }, timeoutMs)
      const cleanup = () => {
        ws.removeEventListener('open', onOpen)
        ws.removeEventListener('close', onClose)
        ws.removeEventListener('error', onError)
        clearTimeout(timer)
        if (!settled) settled = true
      }

      const onOpen = () => {
        cleanup()
        resolve(true)
      }
      const onClose = () => {
        cleanup()
        resolve(false)
      }
      const onError = () => {
        cleanup()
        resolve(false)
      }

      ws.addEventListener('open', onOpen)
      ws.addEventListener('close', onClose)
      ws.addEventListener('error', onError)

      
    })

  // Stable ID for a clicked element across sync updates.
  const buildChipId = (item: ClickedElementStored) => `${item.tabId}:${item.url}:${item.selector}`
  const CHIP_IDS_SEPARATOR = '||'

  // Parse the serialized chip-ids payload from the DOM.
  const parseChipIds = (value?: string | null) =>
    value ? value.split(CHIP_IDS_SEPARATOR).filter(Boolean) : []

  // Render chip label text (no tab prefix; favicon indicates tab).
  const formatChipLabel = (ids: string[]) => {
    const items = ids
      .map((id) => chipMap.get(id))
      .filter(Boolean) as ClickedElementStored[]
    if (items.length === 0) return ''
    const labels = items.map((item) => formatElementShortLabel(item))
    return labels.join(',')
  }

  // Host used to select the favicon when multiple elements are coalesced.
  const getChipHost = (ids: string[]) => {
    const first = ids.map((id) => chipMap.get(id)).find(Boolean) as ClickedElementStored | undefined
    return first ? getHost(first) : 'unknown'
  }

  // Read the full element list for a chip (fallbacks to chip-ids lookup).
  const readChipItems = (chip: HTMLElement): ClickedElementStored[] => {
    const raw = chip.dataset.chipItems
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as ClickedElementStored[]
        if (Array.isArray(parsed)) return parsed
      } catch {
        // ignore
      }
    }
    const ids = parseChipIds(chip.dataset.chipIds)
    return ids.map((id) => chipMap.get(id)).filter(Boolean) as ClickedElementStored[]
  }

  // Persist full items onto DOM so removals can update background consistently.
  const writeChipItems = (chip: HTMLElement, items: ClickedElementStored[]) => {
    chip.dataset.chipItems = JSON.stringify(items)
    const ids = items.map(buildChipId)
    chip.dataset.chipIds = ids.join(CHIP_IDS_SEPARATOR)
  }

  const formatChipLabelFromItems = (items: ClickedElementStored[]) => {
    if (items.length === 0) return ''
    return items.map((item) => formatElementShortLabel(item)).join(',')
  }

  const getChipHostFromItems = (items: ClickedElementStored[]) => {
    if (items.length === 0) return 'unknown'
    return getHost(items[0])
  }

  const serializeEditorForDisplayParts = (): DisplayPart[] => {
    const root = editorRef.current
    if (!root) return []
    const parts: DisplayPart[] = []

    for (const node of Array.from(root.childNodes)) {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = node.textContent || ''
        if (text) parts.push({ type: 'text', content: text })
        continue
      }

      if (node instanceof HTMLElement) {
        const chipIds = parseChipIds(node.dataset.chipIds)
        if (chipIds.length > 0) {
          const items = readChipItems(node)
          if (items.length > 0) parts.push({ type: 'chip', items })
          continue
        }
        const text = node.textContent || ''
        if (text) parts.push({ type: 'text', content: text })
      }
    }

    return parts
  }

  const displayPartsToText = (parts: DisplayPart[]) =>
    parts
      .map((part) => (part.type === 'text' ? part.content : formatChipLabelFromItems(part.items)))
      .join('')

  const chipMap = useMemo(() => {
    const map = new Map<string, ClickedElementStored>()
    clickedElements.forEach((item) => {
      map.set(buildChipId(item), item)
    })
    return map
  }, [clickedElements])

  const fetchHtmlForItem = async (item: ClickedElementStored) => {
    try {
      const response = await chrome.tabs.sendMessage(item.tabId, {
        type: MESSAGE_TYPES.getElementHtml,
        selector: item.tag === 'tab' ? null : item.selector,
      })
      if (typeof response?.html === 'string' && response.html.length > 0) {
        return response.html
      }
    } catch {
      // fall through
    }

    if (item.tag === 'tab') {
      try {
        const results = await chrome.scripting.executeScript({
          target: { tabId: item.tabId },
          func: () => {
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
                'meta',
                'link',
                'form',
                'input',
                'textarea',
                'select',
                'button',
                'video',
                'audio',
                'picture',
                'source',
                'object',
                'embed',
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

              root.querySelectorAll<HTMLElement>('*').forEach((el) => {
                Array.from(el.attributes).forEach((attr) => {
                  const name = attr.name.toLowerCase()
                  if (
                    name.startsWith('on') ||
                    name === 'style' ||
                    name === 'srcset' ||
                    name === 'src' ||
                    name === 'href' ||
                    name === 'integrity' ||
                    name === 'nonce' ||
                    name === 'crossorigin' ||
                    name === 'referrerpolicy'
                  ) {
                    el.removeAttribute(attr.name)
                  }
                })
              })
            }

            const body = document.body?.cloneNode(true) as Element | null
            if (!body) return ''
            sanitizeElementTree(body)
            const html = body.outerHTML
            return html.length > 1000 ? html.slice(0, 1000) : html
          },
        })
        const html = results?.[0]?.result
        return typeof html === 'string' ? html : ''
      } catch {
        return ''
      }
    }

    return ''
  }

  const fetchCssForItem = async (item: ClickedElementStored) => {
    if (item.tag === 'tab') return ''
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: item.tabId },
        func: (selector: string) => {
          const el = document.querySelector(selector)
          if (!el) return ''

          const cssTexts: string[] = []
          const inline = el.getAttribute('style')
          if (inline && inline.trim().length > 0) {
            cssTexts.push(`${selector}{${inline}}`)
          }

          const sheets = Array.from(document.styleSheets)
          for (const sheet of sheets) {
            let rules: CSSRuleList | undefined
            try {
              rules = sheet.cssRules
            } catch {
              continue
            }
            if (!rules) continue
            for (const rule of Array.from(rules)) {
              if (rule.type !== CSSRule.STYLE_RULE) continue
              const styleRule = rule as CSSStyleRule
              try {
                if (el.matches(styleRule.selectorText)) {
                  cssTexts.push(styleRule.cssText)
                }
              } catch {
                // ignore invalid selectors
              }
            }
          }

          return cssTexts.join('\n')
        },
        args: [item.selector],
      })
      const css = results?.[0]?.result
      return typeof css === 'string' ? css : ''
    } catch {
      return ''
    }
  }

  const serializeEditorWithHtml = async () => {
    const root = editorRef.current
    if (!root) return ''
    const parts: string[] = []

    for (const node of Array.from(root.childNodes)) {
      if (node.nodeType === Node.TEXT_NODE) {
        parts.push(node.textContent || '')
        continue
      }
      if (node instanceof HTMLElement) {
        const chipIds = parseChipIds(node.dataset.chipIds)
        if (chipIds.length > 0) {
          const items = readChipItems(node)
          const htmlParts: string[] = []
          for (const item of items) {
            const html = await fetchHtmlForItem(item)
            if (!html) continue
            if (item.tag !== 'tab') {
              const css = await fetchCssForItem(item)
              if (css) {
                htmlParts.push(`<style>${css}</style>\n${html}`)
                continue
              }
            }
            htmlParts.push(html)
          }
          parts.push(htmlParts.join('\n'))
          continue
        }
        parts.push(node.textContent || '')
      }
    }

    return parts.join('')
  }

  const getCaretOffset = (root: HTMLElement) => {
    const selection = window.getSelection()
    if (!selection || selection.rangeCount === 0) return root.textContent?.length ?? 0
    const range = selection.getRangeAt(0)
    const preRange = range.cloneRange()
    preRange.selectNodeContents(root)
    preRange.setEnd(range.endContainer, range.endOffset)
    return preRange.toString().length
  }

  const createRangeFromOffsets = (root: HTMLElement, start: number, end: number) => {
    const range = document.createRange()
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null)
    let current = 0
    let startNode: Node | null = null
    let endNode: Node | null = null
    let startOffset = 0
    let endOffset = 0
    let node = walker.nextNode()

    while (node) {
      const text = node.textContent || ''
      const next = current + text.length
      if (!startNode && start >= current && start <= next) {
        startNode = node
        startOffset = start - current
      }
      if (startNode && end >= current && end <= next) {
        endNode = node
        endOffset = end - current
        break
      }
      current = next
      node = walker.nextNode()
    }

    if (!startNode || !endNode) return null
    range.setStart(startNode, Math.max(0, startOffset))
    range.setEnd(endNode, Math.max(0, endOffset))
    return range
  }

  const updateMentionPosition = () => {
    const root = editorRef.current
    if (!root) return
    const selection = window.getSelection()
    if (!selection || selection.rangeCount === 0) return
    const range = selection.getRangeAt(0).cloneRange()
    range.collapse(true)
    const rect = range.getBoundingClientRect()
    const rootRect = root.getBoundingClientRect()
    if (!rect || (rect.top === 0 && rect.left === 0)) return
    setMentionPosition({
      top: rect.top - rootRect.top,
      left: rect.left - rootRect.left,
    })
  }

  const loadCurrentWindowTabs = async () => {
    const tabs = await chrome.tabs.query({ currentWindow: true })
    const normalized = tabs
      .map((tab) => {
        if (typeof tab.id !== 'number') return null
        const url = tab.url || tab.pendingUrl || ''
        if (!url) return null
        return {
          id: tab.id,
          title: tab.title || url,
          url,
        }
      })
      .filter(Boolean) as TabMatch[]
    tabsCacheRef.current = normalized
    return normalized
  }

  const computeTabMatches = (tabs: TabMatch[], queryText: string) => {
    const q = queryText.trim().toLowerCase()
    if (!q) return tabs.slice(0, 5)

    return tabs
      .map((tab) => {
        const title = tab.title.toLowerCase()
        const url = tab.url.toLowerCase()
        const titleIndex = title.indexOf(q)
        const urlIndex = url.indexOf(q)
        if (titleIndex === -1 && urlIndex === -1) return null
        const score = titleIndex !== -1 ? titleIndex : 1000 + urlIndex
        return { tab, score }
      })
      .filter(Boolean)
      .sort((a, b) => {
        if (!a || !b) return 0
        if (a.score !== b.score) return a.score - b.score
        return a.tab.title.length - b.tab.title.length
      })
      .map((item) => item!.tab)
      .slice(0, 5)
  }

  const closeMention = () => {
    setMentionOpen(false)
    setTabMatches([])
    setMentionSelectedIndex(0)
    mentionRangeRef.current = null
  }

  const refreshMentionMatches = async (queryText: string) => {
    const tabs = tabsCacheRef.current.length > 0 ? tabsCacheRef.current : await loadCurrentWindowTabs()
    const matches = computeTabMatches(tabs, queryText)
    setTabMatches(matches)
    setMentionSelectedIndex(0)
  }

  const selectTabMatch = async (tab: TabMatch) => {
    const url = tab.url
    const payload = {
      tag: 'tab',
      id: null,
      classes: [],
      selector: `tab:${tab.id}`,
      url,
      timestamp: Date.now(),
      tabTitle: tab.title,
    }
    const item: ClickedElementStored = {
      ...payload,
      tabId: tab.id,
    }

    const root = editorRef.current
    const rangeInfo = mentionRangeRef.current
    if (root && rangeInfo) {
      const range = createRangeFromOffsets(root, rangeInfo.start, rangeInfo.end)
      if (range) {
        const selection = window.getSelection()
        selection?.removeAllRanges()
        selection?.addRange(range)
      }
    }

    insertChipAtCursor(item)
    closeMention()

    await chrome.runtime.sendMessage({
      type: MESSAGE_TYPES.storeClicked,
      tabId: tab.id,
      payload,
    })
  }

  const handleEditorInput = () => {
    const root = editorRef.current
    if (!root) return
    const text = root.textContent ?? ''
    setQuery(text)
    const caretOffset = getCaretOffset(root)
    const beforeCaret = text.slice(0, caretOffset)
    const atIndex = beforeCaret.lastIndexOf('@')
    if (atIndex === -1) {
      closeMention()
      return
    }

    if (atIndex > 0 && !/\s/.test(beforeCaret[atIndex - 1])) {
      closeMention()
      return
    }

    const queryText = beforeCaret.slice(atIndex + 1)
    if (/\s/.test(queryText)) {
      closeMention()
      return
    }

    mentionRangeRef.current = { start: atIndex, end: caretOffset }
    setMentionOpen(true)
    void refreshMentionMatches(queryText)
    updateMentionPosition()
  }

  // Insert or merge a chip at cursor; merges with adjacent chip to coalesce.
  const insertChipAtCursor = (item: ClickedElementStored) => {
    const root = editorRef.current
    if (!root) return
    const chipId = buildChipId(item)

    if (root.querySelector(`[data-chip-ids*="${CSS.escape(chipId)}"]`)) return

    const ensureTrailingSpace = (target: HTMLElement) => {
      const next = target.nextSibling
      if (!next || (next.nodeType === Node.TEXT_NODE && (next.textContent || '') === '')) {
        target.after(document.createTextNode(' '))
      }
    }

    const removeTrailingSpace = (target: HTMLElement) => {
      const next = target.nextSibling
      if (next && next.nodeType === Node.TEXT_NODE && (next.textContent || '').trim() === '') {
        next.remove()
      }
    }

    const mergeInto = (target: HTMLElement) => {
      const existingItems = readChipItems(target)
      if (existingItems.some((existing) => buildChipId(existing) === chipId)) return
      const nextItems = [...existingItems, item]
      writeChipItems(target, nextItems)
      const nextIds = nextItems.map(buildChipId)
      const labelEl = target.querySelector<HTMLElement>('.context-chip-label')
      const iconEl = target.querySelector<HTMLImageElement>('.context-chip-icon')
      if (labelEl) {
        labelEl.textContent = formatChipLabel(nextIds)
      }
      if (iconEl) {
        const host = getChipHost(nextIds)
        iconEl.src = getFaviconUrl(host)
        iconEl.alt = host
      }
      ensureTrailingSpace(target)
      setQuery(root.textContent ?? '')
      root.focus()
    }

    const selection = window.getSelection()

    const getLastMeaningfulNode = () => {
      for (let i = root.childNodes.length - 1; i >= 0; i -= 1) {
        const node = root.childNodes[i]
        if (node.nodeType === Node.TEXT_NODE) {
          if ((node.textContent || '').trim() === '') continue
          return node
        }
        return node
      }
      return null
    }

    const isCursorAtEnd = () => {
      if (!selection || selection.rangeCount === 0 || !root.contains(selection.anchorNode)) return true
      const range = selection.getRangeAt(0)
      const container = range.startContainer
      const offset = range.startOffset
      if (container === root) {
        return offset === root.childNodes.length
      }
      if (container.nodeType === Node.TEXT_NODE) {
        const text = container.textContent || ''
        return offset >= text.length && container.nextSibling === null
      }
      if (container.nodeType === Node.ELEMENT_NODE) {
        const element = container as Element
        return offset >= element.childNodes.length && element.nextSibling === null
      }
      return false
    }

    const lastMeaningful = getLastMeaningfulNode()
    if (lastMeaningful instanceof HTMLElement && lastMeaningful.dataset.chipIds && isCursorAtEnd()) {
      mergeInto(lastMeaningful)
      return
    }

    let previousNode: Node | null = null
    if (selection && selection.rangeCount > 0 && root.contains(selection.anchorNode)) {
      const range = selection.getRangeAt(0)
      const container = range.startContainer
      const offset = range.startOffset
      if (container.nodeType === Node.TEXT_NODE) {
        const text = container.textContent || ''
        if (offset === 0) {
          previousNode = container.previousSibling
        } else if (offset === text.length && text.trim() === '') {
          previousNode = container.previousSibling
        }
      } else if (container.nodeType === Node.ELEMENT_NODE) {
        const element = container as Element
        if (offset > 0) {
          previousNode = element.childNodes[offset - 1]
        }
      }
    }

    if (previousNode?.nodeType === Node.TEXT_NODE && (previousNode.textContent || '').trim() === '') {
      const possibleChip = previousNode.previousSibling
      if (possibleChip instanceof HTMLElement && possibleChip.dataset.chipIds) {
        mergeInto(possibleChip)
        return
      }
    }

    if (previousNode instanceof HTMLElement && previousNode.dataset.chipIds) {
      mergeInto(previousNode)
      return
    }

    const chip = document.createElement('span')
    chip.className = 'context-chip'
    writeChipItems(chip, [item])
    chip.contentEditable = 'false'

    const icon = document.createElement('img')
    icon.className = 'context-chip-icon'
    const host = getHost(item)
    icon.src = getFaviconUrl(host)
    icon.alt = host
    chip.appendChild(icon)

    const label = document.createElement('span')
    label.className = 'context-chip-label'
    label.textContent = formatElementShortLabel(item)
    chip.appendChild(label)

    const remove = document.createElement('button')
    remove.type = 'button'
    remove.className = 'context-chip-remove'
    remove.textContent = '×'
    remove.title = 'Remove element'
    remove.setAttribute('aria-label', 'Remove element')
    remove.addEventListener('click', () => {
      const itemsToRemove = readChipItems(chip)
      void removeClickedElementsBulk(itemsToRemove)
      removeTrailingSpace(chip)
      chip.remove()
      setQuery(root.textContent ?? '')
    })
    chip.appendChild(remove)

    const space = document.createTextNode(' ')

    if (!selection || selection.rangeCount === 0 || !root.contains(selection.anchorNode)) {
      root.appendChild(chip)
      root.appendChild(space)
      root.focus()
      setQuery(root.textContent ?? '')
      return
    }

    const range = selection.getRangeAt(0)
    range.deleteContents()
    range.insertNode(space)
    range.insertNode(chip)

    const nextRange = document.createRange()
    nextRange.setStartAfter(space)
    nextRange.collapse(true)
    selection.removeAllRanges()
    selection.addRange(nextRange)

    setQuery(root.textContent ?? '')
    root.focus()
  }

  // Remove a chip from the editor when background removes a selection.
  const removeChipNode = (chipId: string) => {
    const root = editorRef.current
    if (!root) return
    const chips = Array.from(root.querySelectorAll<HTMLElement>('[data-chip-ids]'))
    chips.forEach((chip) => {
      const items = readChipItems(chip)
      const ids = items.map(buildChipId)
      if (!ids.includes(chipId)) return
      const nextItems = items.filter((item) => buildChipId(item) !== chipId)
      const nextIds = nextItems.map(buildChipId)
      if (nextItems.length === 0) {
        const next = chip.nextSibling
        if (next && next.nodeType === Node.TEXT_NODE && (next.textContent || '').trim() === '') {
          next.remove()
        }
        chip.remove()
      } else {
        writeChipItems(chip, nextItems)
        const labelEl = chip.querySelector<HTMLElement>('.context-chip-label')
        const iconEl = chip.querySelector<HTMLImageElement>('.context-chip-icon')
        if (labelEl) {
          labelEl.textContent = formatChipLabel(nextIds)
        }
        if (iconEl) {
          const host = getChipHost(nextIds)
          iconEl.src = getFaviconUrl(host)
          iconEl.alt = host
        }
      }
    })
    setQuery(root.textContent ?? '')
  }

  // Bulk remove so background updates highlights & storage atomically.
  const removeClickedElementsBulk = async (items: ClickedElementStored[]) => {
    if (items.length === 0) return
    await chrome.runtime.sendMessage({
      type: MESSAGE_TYPES.removeClickedBulk,
      items: items.map((item) => ({
        selector: item.selector,
        url: item.url,
        tabId: item.tabId,
      })),
    })
  }


  const refreshClickedElements = async () => {
    const response = await chrome.runtime.sendMessage({
      type: MESSAGE_TYPES.getAllClicked,
    })
    const elements = Array.isArray(response?.elements) ? (response.elements as ClickedElementStored[]) : []
    setClickedElements(elements)
  }

  useEffect(() => {
    const initContext = async () => {
      await refreshClickedElements()
    }

    void initContext()

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        void refreshClickedElements()
      }
    }

    const handleRuntimeMessage = (message: { type?: string; elements?: ClickedElementStored[] }) => {
      if (message?.type !== MESSAGE_TYPES.clickedElementsUpdated) return
      if (Array.isArray(message.elements)) {
        setClickedElements(message.elements)
        return
      }
      void refreshClickedElements()
    }

    document.addEventListener('visibilitychange', handleVisibility)
    chrome.runtime.onMessage.addListener(handleRuntimeMessage)

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility)
      chrome.runtime.onMessage.removeListener(handleRuntimeMessage)
    }
  }, [])

  useEffect(() => {
    const nextIds = new Set(clickedElements.map(buildChipId))
    const known = knownChipIdsRef.current

    clickedElements.forEach((item) => {
      const id = buildChipId(item)
      if (!known.has(id)) {
        insertChipAtCursor(item)
      }
    })

    known.forEach((id) => {
      if (!nextIds.has(id)) {
        removeChipNode(id)
      }
    })

    knownChipIdsRef.current = nextIds
  }, [clickedElements])

  useEffect(() => {
    if (!mentionOpen || !mentionPosition) return
    const root = editorRef.current
    const tooltip = mentionTooltipRef.current
    if (!root || !tooltip) return
    const rootRect = root.getBoundingClientRect()
    const tooltipWidth = tooltip.offsetWidth
    if (!tooltipWidth || !rootRect.width) return
    const padding = 8
    const maxLeft = Math.max(padding, rootRect.width - tooltipWidth - padding)
    const clampedLeft = Math.min(Math.max(mentionPosition.left, padding), maxLeft)
    if (clampedLeft !== mentionPosition.left) {
      setMentionPosition({ ...mentionPosition, left: clampedLeft })
    }
  }, [mentionOpen, mentionPosition])

  useEffect(() => {
    let activeTabId: number | null = null

    const notifyOpen = async () => {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      activeTabId = tab?.id ?? null
      await chrome.runtime.sendMessage({
        type: MESSAGE_TYPES.sidepanelOpen,
        tabId: activeTabId,
      })
    }

    void notifyOpen()

    return () => {
      if (!activeTabId) return
      chrome.runtime.sendMessage({
        type: MESSAGE_TYPES.sidepanelClose,
        tabId: activeTabId,
      })
    }
  }, [])

  // WebSocket connection management — one connection per active project
  useEffect(() => {
    if (!activeProject) {
      wsRef.current = null
      return
    }

    const ws = new WebSocket(`${WS_URL}/ws/${activeProject.id}`)
    wsRef.current = ws

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        // Backend is asking us to fetch a tab's page content
        if (data.type === 'request_tab_content') {
          const tabId = data.tab_id as number
          const requestId = data.request_id as string
          const includeHtml = Boolean(data.include_html)

          const sendContent = (content: string) => {
            ws.send(
              JSON.stringify({
                type: 'tab_content_response',
                request_id: requestId,
                content,
              }),
            )
          }

          // Try the content-script message first; fall back to
          // chrome.scripting.executeScript for tabs where the content
          // script isn't injected (e.g. tabs open before extension load).
          chrome.tabs
            .sendMessage(tabId, {
              type: MESSAGE_TYPES.getPageContent,
              includeHtml,
            })
            .then((response) => {
              sendContent(response?.content ?? '(no content)')
            })
            .catch(() => {
              // Fallback: programmatically inject a tiny script to grab content.
              // Full content is sent; pagination / chunking happens server-side.
              chrome.scripting
                .executeScript({
                  target: { tabId },
                  func: (html: boolean) =>
                    html
                      ? (document.body?.innerHTML ?? '')
                      : (document.body?.innerText ?? ''),
                  args: [includeHtml],
                })
                .then((results) => {
                  const text = results?.[0]?.result ?? '(no content)'
                  sendContent(text)
                })
                .catch(() => {
                  sendContent(
                    `Error: could not reach tab ${tabId} (page may be a chrome:// URL or not yet loaded)`,
                  )
                })
            })
          return
        }

        // Backend generated a title for a conversation
        if (data.type === 'conversation_title') {
          setConversations((prev) =>
            prev.map((c) =>
              c.id === data.conversation_id ? { ...c, title: data.title } : c,
            ),
          )
          return
        }

        messageHandlerRef.current?.(data)
      } catch {
        // ignore malformed messages
      }
    }

    ws.onerror = () => {
      setError('WebSocket connection error')
    }

    ws.onclose = () => {
      // If we were mid-stream, treat as error
      if (messageHandlerRef.current) {
        setError('Connection lost')
        setLoading(false)
        messageHandlerRef.current = null
      }
      wsRef.current = null
    }

    return () => {
      messageHandlerRef.current = null
      ws.close()
      wsRef.current = null
    }
  }, [activeProject?.id])

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // --- Project operations ---

  const fetchProjects = async () => {
    try {
      const res = await fetch(`${API_URL}/projects`)
      if (res.ok) {
        const data: Project[] = await res.json()
        setProjects(data)
      }
    } catch {
      // Silently fail
    }
  }

  const handleCreateProject = async () => {
    const name = newProjectName.trim()
    if (!name) return

    setCreatingProject(true)
    try {
      const res = await fetch(`${API_URL}/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (res.ok) {
        const project: Project = await res.json()
        setNewProjectName('')
        setProjects((prev) => [project, ...prev])
        selectProject(project)
      }
    } catch {
      setError('Failed to create project')
    } finally {
      setCreatingProject(false)
    }
  }

  const handleDeleteProject = async (projectId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('Delete this project and all its conversations?')) return

    try {
      const res = await fetch(`${API_URL}/projects/${projectId}`, { method: 'DELETE' })
      if (res.ok) {
        setProjects((prev) => prev.filter((p) => p.id !== projectId))
        if (activeProject?.id === projectId) {
          setActiveProject(null)
          setConversations([])
          setActiveConversationId(null)
          setMessages([])
          setSidebarView('projects')
        }
      }
    } catch {
      setError('Failed to delete project')
    }
  }

  const selectProject = (project: Project) => {
    setActiveProject(project)
    setActiveConversationId(null)
    setMessages([])
    setError('')
    setSidebarView('conversations')
    fetchConversations(project.id)
  }

  // --- Conversation operations ---

  const fetchConversations = async (projectId: string) => {
    try {
      const res = await fetch(`${API_URL}/projects/${projectId}/conversations`)
      if (res.ok) {
        const data: Conversation[] = await res.json()
        setConversations(data)
      }
    } catch {
      // Silently fail
    }
  }

  const loadConversation = async (conversationId: string) => {
    setActiveConversationId(conversationId)
    setError('')
    setSidebarOpen(false)

    try {
      const res = await fetch(`${API_URL}/conversations/${conversationId}`)
      if (res.ok) {
        const data: Message[] = await res.json()
        setMessages(data)
      }
    } catch {
      setError('Failed to load conversation')
    }
  }

  const startNewConversation = () => {
    setActiveConversationId(null)
    setMessages([])
    setQuery('')
    setError('')
    setSidebarOpen(false)
    editorRef.current?.focus()
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const ws = wsRef.current
    const displayParts = serializeEditorForDisplayParts()
    const displayText = displayPartsToText(displayParts).trim()
    const rawMessage = (await serializeEditorWithHtml()).trim()
    if (!rawMessage || loading || !activeProject) return
    if (!ws) {
      setError('Not connected to server yet')
      return
    }
    if (ws.readyState !== WebSocket.OPEN) {
      const opened = await waitForSocketOpen(ws)
      if (!opened) {
        setError('WebSocket not connected')
        return
      }
    }

    const finalQuery = rawMessage
    const chipItems = displayParts
      .filter((part): part is { type: 'chip'; items: ClickedElementStored[] } => part.type === 'chip')
      .flatMap((part) => part.items)
    if (chipItems.length > 0) {
      const uniqueById = new Map<string, ClickedElementStored>()
      chipItems.forEach((item) => uniqueById.set(buildChipId(item), item))
      const uniqueItems = Array.from(uniqueById.values())
      try {
        await removeClickedElementsBulk(uniqueItems)
        const removeSet = new Set(uniqueItems.map(buildChipId))
        setClickedElements((prev) => prev.filter((item) => !removeSet.has(buildChipId(item))))
      } catch {
        // best-effort; continue sending
      }
    }
    setQuery('')
    if (editorRef.current) {
      editorRef.current.textContent = ''
    }
    setLoading(true)
    setError('')

    // Optimistically add user message
    const now = new Date().toISOString()
    setMessages((prev) => [
      ...prev,
      {
        role: 'user',
        content: displayText || rawMessage,
        created_at: now,
        displayParts: displayParts.length > 0 ? displayParts : undefined,
      },
    ])
    let activeTabs: { id: number; url: string; title: string; active: boolean }[] = []

    try {
      const tabs = await chrome.tabs.query({})
      activeTabs = tabs
        .filter((t) => t.id != null && t.url)
        .map((t) => ({
          id: t.id as number,
          url: t.url ?? '',
          title: t.title ?? '',
          active: t.active ?? false,
        }))
      // Cache tab info for resolving tool labels later
      const map = new Map<number, TabInfo>()
      for (const t of tabs) {
        if (t.id != null) {
          map.set(t.id, {
            id: t.id,
            url: t.url ?? '',
            title: t.title ?? '',
            favIconUrl: t.favIconUrl,
          })
        }
      }
      tabsMapRef.current = map
    } catch {
      // Non-critical — proceed without tab info
    }

    // --- Per-request streaming state (closed over by the message handler) ---
    let accumulated = ''
    let assistantAdded = false
    const parts: MessagePart[] = []

    const ensureAssistantMessage = () => {
      if (!assistantAdded) {
        assistantAdded = true
        setLoading(false)
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: accumulated,
            created_at: new Date().toISOString(),
            parts: parts.map((p) => ({ ...p })),
          },
        ])
      }
    }

    const updateAssistantMessage = () => {
      const snap = {
        content: accumulated,
        parts: parts.map((p) => ({ ...p })),
      }
      setMessages((prev) => {
        const updated = [...prev]
        updated[updated.length - 1] = { ...updated[updated.length - 1], ...snap }
        return updated
      })
    }

    // Install a message handler for this request's lifetime
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    messageHandlerRef.current = (data: Record<string, any>) => {
      // Conversation id (first event for new conversations)
      if (data.type === 'conversation_id') {
        if (!activeConversationId) {
          setActiveConversationId(data.conversation_id)
          fetchConversations(activeProject.id)
        }
        return
      }

      // Tool started
      if (data.type === 'tool_start') {
        const { label, favIconUrl } = getToolLabel(data.name, data.args || {}, tabsMapRef.current)
        parts.push({ type: 'tool', name: data.name, label, status: 'running', favIconUrl })
        ensureAssistantMessage()
        updateAssistantMessage()
        return
      }

      // Tool finished
      if (data.type === 'tool_end') {
        for (let k = parts.length - 1; k >= 0; k--) {
          const p = parts[k]
          if (p.type === 'tool' && p.name === data.name && p.status === 'running') {
            p.status = 'done'
            break
          }
        }
        updateAssistantMessage()
        return
      }

      // Streaming content chunk
      if (data.type === 'content') {
        accumulated += data.content
        const last = parts[parts.length - 1]
        if (last && last.type === 'text') {
          last.content += data.content
        } else {
          parts.push({ type: 'text', content: data.content })
        }
        ensureAssistantMessage()
        updateAssistantMessage()
        return
      }

      // Error from backend
      if (data.type === 'error') {
        setError(data.message || 'Something went wrong')
        setLoading(false)
        messageHandlerRef.current = null
        if (!assistantAdded) {
          setMessages((prev) => {
            if (prev.length > 0 && prev[prev.length - 1].role === 'user') {
              return prev.slice(0, -1)
            }
            return prev
          })
        }
        return
      }

      // Done — agent finished responding
      if (data.type === 'done') {
        const finalContent = data.content || accumulated
        accumulated = finalContent
        for (const p of parts) {
          if (p.type === 'tool') p.status = 'done'
        }
        if (!assistantAdded) {
          assistantAdded = true
          setMessages((prev) => [
            ...prev,
            {
              role: 'assistant',
              content: finalContent,
              created_at: new Date().toISOString(),
              parts: parts.map((p) => ({ ...p })),
            },
          ])
        } else {
          updateAssistantMessage()
        }
        if (!activeConversationId && data.conversation_id) {
          setActiveConversationId(data.conversation_id)
          fetchConversations(activeProject.id)
        }
        setLoading(false)
        messageHandlerRef.current = null
      }
    }

    // Send the chat message over the WebSocket
    ws.send(
      JSON.stringify({
        type: 'chat',
        query: finalQuery,
        conversation_id: activeConversationId,
        active_tabs: activeTabs,
      }),
    )
  }

  // Handle Enter submit + backspace/delete chip removal in contenteditable.
  const handleEditorKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (mentionOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault()
      if (tabMatches.length === 0) return
      const delta = e.key === 'ArrowDown' ? 1 : -1
      setMentionSelectedIndex((prev) => {
        const next = (prev + delta + tabMatches.length) % tabMatches.length
        return next
      })
      return
    }

    if (mentionOpen && e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const selected = tabMatches[mentionSelectedIndex] ?? tabMatches[0]
      if (selected) {
        void selectTabMatch(selected)
      } else {
        closeMention()
      }
      return
    }

    if (mentionOpen && e.key === 'Escape') {
      e.preventDefault()
      closeMention()
      return
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e)
      return
    }

    if (e.key !== 'Backspace' && e.key !== 'Delete') return

    const selection = window.getSelection()
    if (!selection || !selection.isCollapsed) return
    const range = selection.getRangeAt(0)
    const container = range.startContainer
    const offset = range.startOffset

    const resolveChip = (node: Node | null) => {
      if (!node) return null
      if (node instanceof HTMLElement && node.dataset.chipIds) return node
      if (node.parentElement?.dataset?.chipIds) return node.parentElement
      return null
    }

    let targetNode: Node | null = null
    if (container.nodeType === Node.TEXT_NODE) {
      if (e.key === 'Backspace' && offset === 0) {
        targetNode = container.previousSibling
      }
      if (e.key === 'Delete' && offset === (container.textContent || '').length) {
        targetNode = container.nextSibling
      }
    } else if (container.nodeType === Node.ELEMENT_NODE) {
      const element = container as Element
      if (e.key === 'Backspace' && offset > 0) {
        targetNode = element.childNodes[offset - 1]
      }
      if (e.key === 'Delete') {
        targetNode = element.childNodes[offset]
      }
    }

    const chip = resolveChip(targetNode)
    if (!chip) return
    const itemsToRemove = readChipItems(chip)
    if (itemsToRemove.length === 0) return
    e.preventDefault()
    void removeClickedElementsBulk(itemsToRemove)
    const next = chip.nextSibling
    if (next && next.nodeType === Node.TEXT_NODE && (next.textContent || '').trim() === '') {
      next.remove()
    }
    chip.remove()
    setQuery(editorRef.current?.textContent ?? '')
  }

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

    if (diffDays === 0) {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    } else if (diffDays === 1) {
      return 'Yesterday'
    } else if (diffDays < 7) {
      return date.toLocaleDateString([], { weekday: 'short' })
    }
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }

  const goBackToProjects = () => {
    setSidebarView('projects')
  }

  return (
    <div className="app">
      {/* Sidebar overlay */}
      {sidebarOpen && <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />}

      {/* Sidebar */}
      <div className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        {sidebarView === 'projects' ? (
          <>
            <div className="sidebar-header">
              <h2>Projects</h2>
            </div>
            <div className="project-create">
              <input
                type="text"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreateProject()}
                placeholder="New project name..."
                disabled={creatingProject}
              />
              <button
                className="new-chat-btn"
                onClick={handleCreateProject}
                disabled={creatingProject || !newProjectName.trim()}
                title="Create project"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </button>
            </div>
            <div className="conversation-list">
              {projects.length === 0 && (
                <div className="empty-conversations">No projects yet</div>
              )}
              {projects.map((project) => (
                <button
                  key={project.id}
                  className={`conversation-item ${project.id === activeProject?.id ? 'active' : ''}`}
                  onClick={() => selectProject(project)}
                >
                  <span className="project-name">{project.name}</span>
                  <div className="project-item-actions">
                    <span className="conversation-time">{formatTime(project.created_at)}</span>
                    <button
                      className="delete-btn"
                      onClick={(e) => handleDeleteProject(project.id, e)}
                      title="Delete project"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                  </div>
                </button>
              ))}
            </div>
          </>
        ) : (
          <>
            <div className="sidebar-header">
              <button className="back-btn" onClick={goBackToProjects} title="Back to projects">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="15 18 9 12 15 6" />
                </svg>
              </button>
              <h2>{activeProject?.name ?? 'Conversations'}</h2>
              <button className="new-chat-btn" onClick={startNewConversation} title="New chat">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </button>
            </div>
            <div className="conversation-list">
              {conversations.length === 0 && (
                <div className="empty-conversations">No conversations yet</div>
              )}
              {conversations.map((conv) => (
                <button
                  key={conv.id}
                  className={`conversation-item ${conv.id === activeConversationId ? 'active' : ''}`}
                  onClick={() => loadConversation(conv.id)}
                >
                  <span className="conversation-id">{conv.title || `${conv.id.slice(0, 8)}...`}</span>
                  <span className="conversation-time">{formatTime(conv.created_at)}</span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Main chat area */}
      <div className="chat-main">
        {/* Header */}
        <div className="chat-header">
          <button className="menu-btn" onClick={() => setSidebarOpen(!sidebarOpen)} title="Toggle sidebar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          </button>
          <h1>{activeProject ? activeProject.name : 'AI Chat'}</h1>
          {activeProject && (
            <button className="new-chat-header-btn" onClick={startNewConversation} title="New chat">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
              </svg>
            </button>
          )}
        </div>

        {/* Messages */}
        <div className="messages-container">
          {!activeProject && (
            <div className="empty-chat">
              <div className="empty-chat-icon">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.4">
                  <path d="M2 20h.01M7 20v-4a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v4M12 10V4m0 0L9 7m3-3 3 3" />
                </svg>
              </div>
              <p>Select or create a project to get started</p>
            </div>
          )}
          {activeProject && messages.length === 0 && !loading && (
            <div className="empty-chat">
              <div className="empty-chat-icon">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.4">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              </div>
              <p>Start a new conversation</p>
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`message ${msg.role}`}>
              {msg.role === 'assistant' && msg.parts && msg.parts.length > 0 ? (
                msg.parts.map((part, j) =>
                  part.type === 'tool' ? (
                    <div key={j} className={`tool-call ${part.status}`}>
                      <span className="tool-call-icon">
                        {part.status === 'running' ? (
                          <svg className="tool-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                          </svg>
                        ) : (
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        )}
                      </span>
                      {part.favIconUrl && (
                        <img src={part.favIconUrl} alt="" className="tool-call-favicon" width="14" height="14" />
                      )}
                      <span className="tool-call-label">{part.label}</span>
                    </div>
                  ) : (
                    <div key={j} className="message-content">
                      <ReactMarkdown>{part.content}</ReactMarkdown>
                    </div>
                  )
                )
              ) : (
                <div className="message-content">
                  {msg.role === 'assistant' ? (
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  ) : msg.displayParts && msg.displayParts.length > 0 ? (
                    <span className="message-inline-parts">
                      {msg.displayParts.map((part, j) =>
                        part.type === 'text' ? (
                          <span key={j}>{part.content}</span>
                        ) : (
                          <span key={j} className="context-chip message-chip" title={formatChipLabelFromItems(part.items)}>
                            <img
                              className="context-chip-icon"
                              src={getFaviconUrl(getChipHostFromItems(part.items))}
                              alt={formatChipLabelFromItems(part.items)}
                            />
                            <span className="context-chip-label">{formatChipLabelFromItems(part.items)}</span>
                          </span>
                        )
                      )}
                    </span>
                  ) : (
                    msg.content
                  )}
                </div>
              )}
            </div>
          ))}
          {loading && (
            <div className="message assistant">
              <div className="message-content typing">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Error */}
        {error && <div className="error">{error}</div>}

        {/* Input */}
        <form onSubmit={handleSubmit} className="chat-input">
          <div className="chat-input-main">
            <div className="chat-input-field">
              <div
                ref={editorRef}
                className="chat-input-editor"
                contentEditable={Boolean(activeProject) && !loading}
                data-placeholder={activeProject ? 'Type a message...' : 'Select a project first...'}
                onInput={handleEditorInput}
                onKeyDown={handleEditorKeyDown}
                suppressContentEditableWarning
              />
              {mentionOpen && mentionPosition && (
                <div
                  className="mention-tooltip"
                  style={{ top: `${mentionPosition.top}px`, left: `${mentionPosition.left}px` }}
                  ref={mentionTooltipRef}
                >
                  <div className="mention-tooltip-title">Tabs</div>
                  {tabMatches.length === 0 ? (
                    <div className="mention-empty">No matches</div>
                  ) : (
                    <div className="mention-list">
                      {tabMatches.map((tab, index) => (
                        <button
                          key={tab.id}
                          type="button"
                          className={`mention-item ${index === mentionSelectedIndex ? 'active' : ''}`}
                          onMouseEnter={() => setMentionSelectedIndex(index)}
                          onMouseDown={(event) => {
                            event.preventDefault()
                            void selectTabMatch(tab)
                          }}
                        >
                          <img
                            className="mention-favicon"
                            src={getFaviconUrl(getHostFromUrl(tab.url))}
                            alt={tab.title}
                          />
                          <div className="mention-item-main">
                            <div className="mention-item-title">{tab.title}</div>
                            <div className="mention-item-url">{tab.url}</div>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
          <button
            type="submit"
            className="send-btn"
            disabled={loading || !query.trim() || !activeProject}
            title="Send message"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </form>
      </div>
    </div>
  )
}
