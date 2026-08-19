# Evolve Extension

Chrome extension for Evolve Browser, built with React, TypeScript, Vite, and the CRXJS Vite plugin.

## Features

- React with TypeScript
- Vite build tool
- CRXJS Vite plugin integration
- Chrome extension manifest configuration

## Quick Start

1. Install dependencies:

```bash
npm install
```

2. Start development server:

```bash
npm run dev
```

3. Open Chrome and navigate to `chrome://extensions/`, enable "Developer mode", and load the unpacked extension from the `dist` directory.

4. Build for production:

```bash
npm run build
```

## Project Structure

- `src/popup/` - Extension popup UI
- `src/content/` - Content scripts
- `src/background.ts` - Service worker (background) logic
- `manifest.config.ts` - Chrome extension manifest configuration

## Documentation

- [React Documentation](https://reactjs.org/)
- [Vite Documentation](https://vitejs.dev/)
- [CRXJS Documentation](https://crxjs.dev/vite-plugin)

## Chrome Extension Development Notes

- Use `manifest.config.ts` to configure your extension
- The CRXJS plugin automatically handles manifest generation
- Content scripts should be placed in `src/content/`
- Popup UI should be placed in `src/popup/`

## Background + Content Script Flow

- The background service worker in `src/background.ts` opens the side panel when the extension icon is clicked.
- The content script in `src/content/highlighter.ts` sends messages to the background worker to:
	- store clicked element metadata in `chrome.storage.local`
	- remove a single clicked element
	- fetch stored clicked elements for the current tab + URL
	- clear all clicked elements for the current tab + URL (Escape key)
- The background worker scopes stored elements by tab + URL and cleans them up when a tab closes.
