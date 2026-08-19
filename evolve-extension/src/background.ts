// Background owns the single source of truth for clicked elements and sidepanel state.
import {
  CLICKED_ELEMENTS_KEY,
  MESSAGE_TYPES,
  type ClickedElementPayload,
  type ClickedElementStored,
  type MessageType,
} from './shared/messages'

type StorageState = {
  [CLICKED_ELEMENTS_KEY]?: ClickedElementStored[]
}

type SidePanelState = {
  tabId?: number
  isOpen?: boolean
}

type ConsoleLogEntry = {
  timestamp: number
  level: 'log' | 'info' | 'warn' | 'error' | 'debug'
  message: string
  url?: string
}

type ConsoleLogFilters = {
  since?: number
  levels?: Array<ConsoleLogEntry['level']>
  limit?: number
}

function getStoredList(stored: StorageState): ClickedElementStored[] {
  const existing = stored[CLICKED_ELEMENTS_KEY]
  return Array.isArray(existing) ? existing : []
}

function dedupeBySelector(list: ClickedElementStored[]) {
  const seen = new Set<string>()
  const next: ClickedElementStored[] = []
  for (const item of list) {
    const key = `${item?.tabId ?? ''}::${item?.url ?? ''}::${item?.selector ?? ''}`
    if (!item?.selector || seen.has(key)) continue
    seen.add(key)
    next.push(item)
  }
  return next
}

function respondOk(sendResponse: (response: { ok: boolean }) => void) {
  sendResponse({ ok: true })
}

function respondNoTab(sendResponse: (response: { ok: boolean } | { elements: ClickedElementStored[] }) => void) {
  sendResponse({ ok: false })
}

function sendSidepanelState(tabId: number, isOpen: boolean) {
  chrome.tabs.sendMessage(tabId, {
    type: isOpen ? MESSAGE_TYPES.sidepanelOpen : MESSAGE_TYPES.sidepanelClose,
  })
}

async function broadcastSidepanelState(isOpen: boolean) {
  const tabs = await chrome.tabs.query({})
  await Promise.allSettled(
    tabs
      .filter((tab) => typeof tab.id === 'number')
      .map((tab) =>
        chrome.tabs.sendMessage(tab.id as number, {
          type: isOpen ? MESSAGE_TYPES.sidepanelOpen : MESSAGE_TYPES.sidepanelClose,
        }),
      ),
  )
}

function sendUnhighlightSelector(tabId: number, selector: string) {
  chrome.tabs.sendMessage(tabId, {
    type: MESSAGE_TYPES.unhighlightSelector,
    selector,
  })
}

function sendHighlightSelector(tabId: number, selector: string) {
  chrome.tabs.sendMessage(tabId, {
    type: MESSAGE_TYPES.highlightSelector,
    selector,
  })
}

function sendClearHighlightsDom(tabId: number) {
  chrome.tabs.sendMessage(tabId, {
    type: MESSAGE_TYPES.clearHighlightsDom,
  })
}

async function broadcastClearHighlightsDom() {
  const tabs = await chrome.tabs.query({})
  await Promise.allSettled(
    tabs
      .filter((tab) => typeof tab.id === 'number')
      .map((tab) =>
        chrome.tabs.sendMessage(tab.id as number, {
          type: MESSAGE_TYPES.clearHighlightsDom,
        }),
      ),
  )
}

async function broadcastClickedElementsUpdated(elements: ClickedElementStored[]) {
  try {
    chrome.runtime.sendMessage({
      type: MESSAGE_TYPES.clickedElementsUpdated,
      elements,
    })
  } catch {
    // Ignore runtime listeners that are unavailable
  }

  const tabs = await chrome.tabs.query({})
  await Promise.allSettled(
    tabs
      .filter((tab) => typeof tab.id === 'number')
      .map((tab) =>
        chrome.tabs.sendMessage(tab.id as number, {
          type: MESSAGE_TYPES.clickedElementsUpdated,
          elements,
        }),
      ),
  )
}

const sidepanelStateByTab = new Map<number, boolean>()
let isSidepanelOpen = false

const consoleLogsByTab = new Map<number, ConsoleLogEntry[]>()
const MAX_CONSOLE_LOGS_PER_TAB = 500

function appendConsoleLog(tabId: number, entry: ConsoleLogEntry) {
  const list = consoleLogsByTab.get(tabId) ?? []
  list.push(entry)
  if (list.length > MAX_CONSOLE_LOGS_PER_TAB) {
    list.splice(0, list.length - MAX_CONSOLE_LOGS_PER_TAB)
  }
  consoleLogsByTab.set(tabId, list)
}

function filterConsoleLogs(tabId: number, filters: ConsoleLogFilters = {}) {
  const list = consoleLogsByTab.get(tabId) ?? []
  const since = typeof filters.since === 'number' ? filters.since : undefined
  const levels = Array.isArray(filters.levels) && filters.levels.length > 0
    ? new Set(filters.levels)
    : null
  const limit = typeof filters.limit === 'number' && filters.limit > 0 ? filters.limit : MAX_CONSOLE_LOGS_PER_TAB

  const filtered = list.filter((entry) => {
    if (since !== undefined && entry.timestamp < since) return false
    if (levels && !levels.has(entry.level)) return false
    return true
  })

  return filtered.slice(-limit)
}

function clearConsoleLogs(tabId: number) {
  consoleLogsByTab.delete(tabId)
}

async function readClickedElements(): Promise<ClickedElementStored[]> {
  const stored: StorageState = await chrome.storage.local.get(CLICKED_ELEMENTS_KEY)
  return dedupeBySelector(getStoredList(stored))
}

async function writeClickedElements(next: ClickedElementStored[]) {
  await chrome.storage.local.set({ [CLICKED_ELEMENTS_KEY]: dedupeBySelector(next) })
}

function removeMatching(
  list: ClickedElementStored[],
  tabId: number,
  selector?: string | null,
  url?: string | null,
) {
  return list.filter(
    (item) =>
      !(
        item?.tabId === tabId &&
        (typeof selector === 'string' ? item?.selector === selector : true) &&
        (typeof url === 'string' ? item?.url === url : true)
      ),
  )
}

// Update storage list and broadcast to all listeners.
async function updateClickedElements(
  updater: (current: ClickedElementStored[]) => ClickedElementStored[],
) {
  const list = await readClickedElements()
  const next = dedupeBySelector(updater(list))
  await writeClickedElements(next)
  await broadcastClickedElementsUpdated(next)
  return next
}

async function setSidepanelState(tabId: number, open: boolean) {
  isSidepanelOpen = open
  sidepanelStateByTab.set(tabId, open)
  sendSidepanelState(tabId, open)
  await broadcastSidepanelState(open)
}


/**
 * Opens the side panel when the extension action is clicked.
 */
chrome.action.onClicked.addListener((tab) => {
  if (tab.id) {
    void setSidepanelState(tab.id, true)
    chrome.sidePanel.open({ tabId: tab.id })
  }
})

const sidePanelApi = chrome.sidePanel as unknown as {
  onStateChanged?: { addListener: (callback: (state: SidePanelState) => void) => void }
}

sidePanelApi.onStateChanged?.addListener((state: SidePanelState) => {
  const tabId = state.tabId
  if (!tabId) return
  const isOpen = Boolean(state.isOpen)
  void setSidepanelState(tabId, isOpen)
})

/**
 * Handles storage-related messages from content scripts.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id ?? message?.tabId

  const type = message?.type as MessageType | undefined

  if (type === MESSAGE_TYPES.getClicked) {
    if (!tabId) {
      sendResponse({ elements: [] })
      return false
    }

    readClickedElements().then((list) => {
      const elements = list.filter((item) => item?.tabId === tabId && item?.url === message?.url)
      sendResponse({ elements })
    })
    return true
  }

  if (type === MESSAGE_TYPES.getAllClicked) {
    readClickedElements().then((elements) => {
      sendResponse({ elements })
    })
    return true
  }

  if (type === MESSAGE_TYPES.storeConsoleLog) {
    if (!tabId) {
      respondNoTab(sendResponse)
      return false
    }

    const payload = message?.payload as ConsoleLogEntry | undefined
    if (!payload || typeof payload.message !== 'string' || typeof payload.timestamp !== 'number') {
      respondOk(sendResponse)
      return false
    }

    appendConsoleLog(tabId, {
      level: payload.level ?? 'log',
      timestamp: payload.timestamp,
      message: payload.message,
      url: payload.url,
    })
    respondOk(sendResponse)
    return false
  }

  if (type === MESSAGE_TYPES.getConsoleLogs) {
    if (!tabId) {
      sendResponse({ logs: [] })
      return false
    }

    const filters: ConsoleLogFilters = {
      since: message?.since,
      levels: message?.levels,
      limit: message?.limit,
    }

    const logs = filterConsoleLogs(tabId, filters)
    sendResponse({ logs })
    return false
  }

  if (type === MESSAGE_TYPES.clearConsoleLogs) {
    if (tabId) clearConsoleLogs(tabId)
    respondOk(sendResponse)
    return false
  }

  if (type === MESSAGE_TYPES.storeClicked) {
    if (!tabId) {
      respondNoTab(sendResponse)
      return false
    }

    const payload = message.payload as ClickedElementPayload
    updateClickedElements((list) => [
      ...removeMatching(list, tabId, payload?.selector, payload?.url),
      { ...payload, tabId },
    ]).then(() => {
      if (typeof payload?.selector === 'string') {
        sendHighlightSelector(tabId, payload.selector)
      }
      respondOk(sendResponse)
    })
    return true
  }

  if (type === MESSAGE_TYPES.removeClicked) {
    if (!tabId) {
      respondNoTab(sendResponse)
      return false
    }

    const selector = message.selector
    const url = message.url
    updateClickedElements((list) => removeMatching(list, tabId, selector, url)).then(() => {
      if (typeof selector === 'string') {
        sendUnhighlightSelector(tabId, selector)
      }
      respondOk(sendResponse)
    })
    return true
  }

  // Bulk removal keeps UI + highlights consistent for coalesced chips.
  if (type === MESSAGE_TYPES.removeClickedBulk) {
    const items = Array.isArray(message?.items) ? message.items : []
    if (items.length === 0) {
      respondOk(sendResponse)
      return false
    }

    updateClickedElements((list) =>
      list.filter(
        (item) =>
          !items.some(
            (removeItem: { selector?: string; url?: string; tabId?: number }) =>
              item?.tabId === removeItem?.tabId &&
              item?.selector === removeItem?.selector &&
              item?.url === removeItem?.url,
          ),
      ),
    ).then(() => {
      items.forEach((removeItem: { selector?: string; tabId?: number }) => {
        if (typeof removeItem?.selector === 'string' && typeof removeItem?.tabId === 'number') {
          sendUnhighlightSelector(removeItem.tabId, removeItem.selector)
        }
      })
      respondOk(sendResponse)
    })
    return true
  }

  if (type === MESSAGE_TYPES.toggleClicked) {
    if (!tabId) {
      respondNoTab(sendResponse)
      return false
    }

    const payload = message.payload as ClickedElementPayload | undefined
    const selector = payload?.selector ?? message.selector
    const url = payload?.url ?? message.url

    updateClickedElements((list) => {
      const exists = list.some(
        (item) => item?.tabId === tabId && item?.selector === selector && item?.url === url,
      )

      if (exists) {
        return removeMatching(list, tabId, selector, url)
      }

      if (!payload || typeof payload.selector !== 'string') {
        return list
      }

      return [...removeMatching(list, tabId, payload.selector, payload.url), { ...payload, tabId }]
    }).then((next) => {
      const exists = next.some(
        (item) => item?.tabId === tabId && item?.selector === selector && item?.url === url,
      )
      if (typeof selector === 'string') {
        if (exists) {
          sendHighlightSelector(tabId, selector)
        } else {
          sendUnhighlightSelector(tabId, selector)
        }
      }
      sendResponse({ ok: true, action: exists ? 'added' : 'removed' })
    })
    return true
  }

  if (type === MESSAGE_TYPES.clearClicked) {
    if (!tabId) {
      respondNoTab(sendResponse)
      return false
    }

    const url = message.url
    updateClickedElements((list) => removeMatching(list, tabId, undefined, url)).then(() => {
      sendClearHighlightsDom(tabId)
      respondOk(sendResponse)
    })
    return true
  }

  if (type === MESSAGE_TYPES.clearAllClicked) {
    updateClickedElements(() => []).then(() => {
      void broadcastClearHighlightsDom()
      respondOk(sendResponse)
    })
    return true
  }

  if (type === MESSAGE_TYPES.getSidepanelState) {
    if (tabId) {
      const known = sidepanelStateByTab.has(tabId)
      const isOpen = known ? Boolean(sidepanelStateByTab.get(tabId)) : false
      sendResponse({ isOpen, known })
      return true
    }
    sendResponse({ isOpen: Boolean(isSidepanelOpen), known: false })
    return true
  }

  if (type === MESSAGE_TYPES.sidepanelOpen) {
    if (!tabId) {
      respondNoTab(sendResponse)
      return false
    }
    void setSidepanelState(tabId, true).then(() => respondOk(sendResponse))
    return true
  }

  if (type === MESSAGE_TYPES.sidepanelClose) {
    if (!tabId) {
      respondNoTab(sendResponse)
      return false
    }
    void setSidepanelState(tabId, false).then(() => respondOk(sendResponse))
    return true
  }

  return false
})

chrome.tabs.onRemoved.addListener((tabId) => {
  consoleLogsByTab.delete(tabId)
  sidepanelStateByTab.delete(tabId)
})

/**
 * Cleans up stored elements when a tab is closed.
 */
chrome.tabs.onRemoved.addListener((tabId) => {
  sidepanelStateByTab.delete(tabId)
  // Filter out entries associated with the closed tab.
  updateClickedElements((list) => list.filter((item) => item?.tabId !== tabId))
})

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  await sendSidepanelState(tabId, isSidepanelOpen)
})

chrome.tabs.onCreated.addListener(async (tab) => {
  if (typeof tab.id !== 'number') return
  await sendSidepanelState(tab.id, isSidepanelOpen)
})
