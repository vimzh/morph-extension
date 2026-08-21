import { defineManifest } from '@crxjs/vite-plugin'
import pkg from './package.json'

export default defineManifest({
  manifest_version: 3,
  name: 'Morph',
  description: 'Build personalized browser features with AI.',
  version: pkg.version,
  icons: {
    16: 'public/morph-logo-16.png',
    32: 'public/morph-logo-32.png',
    48: 'public/logo.png',
    128: 'public/morph-logo-128.png',
  },
  action: {
    default_icon: {
      16: 'public/morph-logo-16.png',
      32: 'public/morph-logo-32.png',
      48: 'public/logo.png',
      128: 'public/morph-logo-128.png',
    },
  },
  background: {
    service_worker: 'src/background.ts',
    type: 'module',
  },
  permissions: [
    'sidePanel',
    'storage',
    'tabs',
    'scripting',
  ],
  host_permissions: [
    '<all_urls>',
  ],
  content_scripts: [{
    js: ['src/content/main.tsx'],
    matches: ['https://*/*', 'http://*/*'],
    run_at: 'document_start', // this is useful so that our scripts (i.e. highlihgting) work as soon as the document start, else we would have to wait until it loads completely.
  }],
  side_panel: {
    default_path: 'src/sidepanel/index.html',
  },
})
