'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// This is the complete renderer capability surface. The private desktop token
// stays in the main process and is never returned through IPC.
contextBridge.exposeInMainWorld('qaDesktop', Object.freeze({
  bootstrap: () => ipcRenderer.invoke('qa:bootstrap'),
  selectFile: kind => ipcRenderer.invoke('qa:select-file', kind),
  openDataDirectory: () => ipcRenderer.invoke('qa:open-data-directory'),
  copyText: text => ipcRenderer.invoke('qa:copy-text', text),
  backup: () => ipcRenderer.invoke('qa:backup'),
  inspectBackup: () => ipcRenderer.invoke('qa:inspect-backup'),
  restoreBackup: () => ipcRenderer.invoke('qa:restore-backup'),
  exportDiagnostics: () => ipcRenderer.invoke('qa:export-diagnostics'),
  restartBackend: () => ipcRenderer.invoke('qa:restart-backend'),
  openLogsDirectory: () => ipcRenderer.invoke('qa:open-logs-directory'),
}))
