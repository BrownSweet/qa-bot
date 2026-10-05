'use strict'

const { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, session, shell } = require('electron')
const { randomBytes } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { BackendProcess, backendLaunch, clipboardHandler, desktopHeaders, isTrustedSender, isTrustedUrl, requestJson, RotatingLog, workspaceCommand } = require('./runtime.cjs')

let mainWindow = null
let backend = null
let backendOrigin = null
let desktopSession = null
let quitting = false
let allowQuit = false
let startupFailure = null
let logStream = null
let smokeExitCode = null
let launchConfig = null
let backendEnvironment = null
let workspaceBusy = false
let recoveryDialogOpen = false
const activeWorkspaceCommands = new Set()
const desktopToken = randomBytes(32).toString('hex')
const smokeMode = process.env.QA_DESKTOP_SMOKE === '1'

if (smokeMode && process.env.QA_DESKTOP_USER_DATA) {
  const smokeData = path.resolve(process.env.QA_DESKTOP_USER_DATA)
  fs.mkdirSync(smokeData, { recursive: true, mode: 0o700 })
  app.setPath('userData', smokeData)
}

function log(source, message) {
  const safe = String(message).split(desktopToken).join('[REDACTED]')
  try { logStream?.write(`${new Date().toISOString()} [${source}] ${safe.endsWith('\n') ? safe : `${safe}\n`}`) }
  catch { process.stderr.write('桌面日志写入失败\n') }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}

function statusPage(title, detail) {
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>智能数据问答</title><style>body{margin:0;background:#f6f8fc;color:#1d2939;font:16px system-ui,sans-serif;display:grid;place-items:center;height:100vh}.panel{max-width:580px;padding:40px}h1{font-size:25px}p{line-height:1.8;color:#536174;white-space:pre-wrap;overflow-wrap:anywhere}</style><main class="panel"><h1>${escapeHtml(title)}</h1><p>${escapeHtml(detail)}</p></main></html>`
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`
}

async function showFailure(error) {
  if (quitting) return
  if (smokeMode && mainWindow && !mainWindow.isDestroyed()) {
    try {
      const contents = mainWindow.webContents
      const snapshot = await contents.executeJavaScript('document.body.innerText.slice(0, 4000)')
      log('smoke-ui', snapshot)
      fs.writeFileSync(path.join(app.getPath('userData'), 'logs', 'smoke-failure.png'), (await contents.capturePage()).toPNG())
    } catch { /* A crashed renderer may not support diagnostics. */ }
  }
  startupFailure = error instanceof Error ? error.message : String(error)
  log('desktop', startupFailure)
  if (mainWindow && !mainWindow.isDestroyed()) {
    await mainWindow.loadURL(statusPage('本地服务未能运行', `${startupFailure}\n\n请重启应用。日志目录：${path.join(app.getPath('userData'), 'logs')}`)).catch(() => {})
    mainWindow.show()
  }
  if (smokeMode) finishSmoke(false, startupFailure)
  else if (!recoveryDialogOpen && !workspaceBusy) {
    recoveryDialogOpen = true
    const choice = await dialog.showMessageBox(mainWindow, { type: 'error', title: '本地工作区需要处理',
      message: '本地服务无法运行', detail: startupFailure,
      buttons: ['恢复完整备份', '导出诊断', '重新启动服务', '暂不处理'], defaultId: 3, cancelId: 3 })
    try {
      if (choice.response === 0) await restoreFromDialog()
      else if (choice.response === 1) await saveWorkspaceFile('diagnostics')
      else if (choice.response === 2) await restartService()
    } catch (failure) { await dialog.showMessageBox(mainWindow, { type: 'error', message: '操作未完成', detail: failure.message }) }
    finally { recoveryDialogOpen = false }
  }
}

function finishSmoke(ok, message) {
  if (smokeExitCode !== null) return
  smokeExitCode = ok ? 0 : 1
  process.stdout.write(`${JSON.stringify({ event: 'QA_DESKTOP_SMOKE', ok, version: app.getVersion(), platform: process.platform, ...(message ? { message } : {}) })}\n`)
  app.quit()
}

function installSessionSecurity() {
  desktopSession = session.fromPartition('qa-desktop')
  desktopSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  desktopSession.setPermissionCheckHandler(() => false)
  desktopSession.setDevicePermissionHandler(() => false)
  desktopSession.webRequest.onBeforeSendHeaders((details, callback) => {
    callback({ requestHeaders: desktopHeaders(details, backendOrigin, desktopToken, mainWindow?.webContents.id) })
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1240, height: 820, minWidth: 960, minHeight: 640,
    title: '智能数据问答', backgroundColor: '#f6f8fc', show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      session: desktopSession,
      sandbox: true, contextIsolation: true, nodeIntegration: false,
      webSecurity: true, allowRunningInsecureContent: false,
    },
  })
  mainWindow.setMenuBarVisibility(true)
  mainWindow.once('ready-to-show', () => mainWindow?.show())
  const contents = mainWindow.webContents
  contents.on('console-message', details => {
    if (smokeMode || details.level === 'error' || details.level === 'warning') log('renderer', details.message)
  })
  contents.setWindowOpenHandler(() => ({ action: 'deny' }))
  contents.on('will-navigate', (event, url) => {
    if (!isTrustedUrl(url, backendOrigin)) event.preventDefault()
  })
  contents.on('will-redirect', (event, url) => {
    if (!isTrustedUrl(url, backendOrigin)) event.preventDefault()
  })
  contents.on('will-frame-navigate', event => {
    if (!isTrustedUrl(event.url, backendOrigin)) event.preventDefault()
  })
  contents.on('will-attach-webview', event => event.preventDefault())
  contents.on('render-process-gone', (_event, details) => {
    showFailure(new Error(`界面进程退出（${details.reason}），请重启应用`))
  })
  mainWindow.on('closed', () => { mainWindow = null })
  return mainWindow
}

function registerIpc() {
  const assertSender = event => {
    if (!isTrustedSender(event, mainWindow, backendOrigin)) throw new Error('拒绝不可信页面访问桌面功能')
    if (startupFailure || quitting) throw new Error('本地服务不可用')
  }
  ipcMain.handle('qa:bootstrap', async event => {
    assertSender(event)
    const result = await requestJson(backendOrigin, '/api/desktop/session', desktopToken)
    if (typeof result.token !== 'string' || !result.user || typeof result.version !== 'string') {
      throw new Error('本地会话初始化失败')
    }
    // Select fields explicitly: future backend fields cannot accidentally expose
    // the private transport secret through this bridge.
    return { token: result.token, user: result.user, version: result.version, platform: process.platform }
  })
  ipcMain.handle('qa:select-file', async (event, kind) => {
    assertSender(event)
    const filters = {
      excel: [{ name: 'Excel 工作簿', extensions: ['xlsx', 'xlsm'] }],
      sqlite: [{ name: 'SQLite 数据库', extensions: ['db', 'sqlite', 'sqlite3', 'db3'] }, { name: '所有文件', extensions: ['*'] }],
    }
    if (typeof kind !== 'string' || !Object.hasOwn(filters, kind)) throw new Error('不支持的文件类型')
    const result = await dialog.showOpenDialog(mainWindow, { title: '选择数据源文件', properties: ['openFile'], filters: filters[kind] })
    return result.canceled ? null : result.filePaths[0] ?? null
  })
  ipcMain.handle('qa:open-data-directory', async event => {
    assertSender(event)
    const error = await shell.openPath(path.join(app.getPath('userData'), 'data'))
    if (error) throw new Error('无法打开数据目录')
    return true
  })
  ipcMain.handle('qa:copy-text', clipboardHandler({ assertSender, writeText: text => clipboard.writeText(text) }))
  ipcMain.handle('qa:backup', event => { assertSender(event); return saveWorkspaceFile('backup') })
  ipcMain.handle('qa:inspect-backup', async event => { assertSender(event); const selected = await selectBackup(); return selected ? runWorkspace('inspect', selected) : null })
  ipcMain.handle('qa:restore-backup', event => { assertSender(event); return restoreFromDialog() })
  ipcMain.handle('qa:export-diagnostics', event => { assertSender(event); return saveWorkspaceFile('diagnostics') })
  ipcMain.handle('qa:restart-backend', event => { assertSender(event); return restartService() })
  ipcMain.handle('qa:open-logs-directory', async event => {
    assertSender(event)
    if (await shell.openPath(path.join(app.getPath('userData'), 'logs'))) throw new Error('无法打开日志目录')
    return true
  })
}

function installRecoveryMenu() {
  const invoke = operation => () => Promise.resolve().then(operation).catch(error => dialog.showMessageBox(mainWindow, { type: 'error', message: '操作未完成', detail: error.message }))
  const workspace = { label: '工作区', submenu: [
    { label: '备份工作区…', click: invoke(() => saveWorkspaceFile('backup')) },
    { label: '恢复备份…', click: invoke(restoreFromDialog) },
    { label: '导出诊断…', click: invoke(() => saveWorkspaceFile('diagnostics')) },
    { label: '打开日志目录', click: invoke(() => shell.openPath(path.join(app.getPath('userData'), 'logs'))) },
    { label: '重新启动本地服务', click: invoke(restartService) },
    { type: 'separator' }, { role: 'quit', label: '退出' },
  ] }
  Menu.setApplicationMenu(Menu.buildFromTemplate(process.platform === 'darwin' ? [{ role: 'appMenu' }, workspace, { role: 'editMenu' }] : [workspace, { role: 'editMenu' }]))
}

function runWorkspace(operation, file, includeSources = false) {
  if (!launchConfig || !backendEnvironment) throw new Error('本地运行环境尚未准备好')
  const task = workspaceCommand(launchConfig, { operation, file, cwd: app.getPath('userData'),
    dataDir: path.join(app.getPath('userData'), 'data'), env: backendEnvironment, includeSources, log })
  activeWorkspaceCommands.add(task)
  task.then(() => activeWorkspaceCommands.delete(task), () => activeWorkspaceCommands.delete(task))
  return task
}

async function selectBackup() {
  const selected = await dialog.showOpenDialog(mainWindow, { title: '选择完整工作区备份', properties: ['openFile'], filters: [{ name: 'QA Robot 备份', extensions: ['zip'] }] })
  return selected.canceled ? null : selected.filePaths[0]
}

async function saveWorkspaceFile(operation) {
  if (workspaceBusy) throw new Error('正在处理工作区，请稍后重试')
  let includeSources = false
  if (operation === 'backup') {
    const choice = await dialog.showMessageBox(mainWindow, { type: 'question', message: '是否将本地 Excel / SQLite 文件一起备份？',
      detail: '完整备份可在另一台机器恢复本地数据源；远程数据库只保留连接配置。备份包含凭证密钥，以及已保存在本机的 SQL、来源路径和每次最多 512 KiB 结果预览，可能含业务敏感数据，请妥善保管。单文件最多 256 MiB，总量最多 1 GiB。',
      buttons: ['包含本地文件', '仅会话和配置', '取消'], defaultId: 0, cancelId: 2 })
    if (choice.response === 2) return null
    includeSources = choice.response === 0
  }
  const chosen = await dialog.showSaveDialog(mainWindow, { title: operation === 'backup' ? '保存备份（包含凭证密钥，请妥善保管）' : '导出诊断（不包含数据库或密钥）',
    defaultPath: `QA-Robot-${operation}-${new Date().toISOString().slice(0, 10)}.zip`, filters: [{ name: 'ZIP 文件', extensions: ['zip'] }] })
  if (chosen.canceled || !chosen.filePath) return null
  if (workspaceBusy || quitting) throw new Error('本地工作区正在处理其他操作')
  workspaceBusy = true
  try { return await runWorkspace(operation, chosen.filePath, includeSources) } finally { workspaceBusy = false }
}

async function restoreFromDialog() {
  if (workspaceBusy) throw new Error('正在处理工作区，请稍后重试')
  const selected = await selectBackup()
  if (!selected) return null
  const info = await runWorkspace('inspect', selected)
  const decision = await dialog.showMessageBox(mainWindow, { type: 'warning', title: '确认恢复工作区',
    message: '将替换本机的会话、数据源配置和 AI 配置，并重启本地服务。',
    detail: `备份时间：${info.createdAt}\n版本：${info.version}\n本地数据文件：${info.externalFilesIncluded ? '已包含，恢复时自动重新定位' : '未包含，换机后需重新定位'}\n缺失文件引用：${info.missingExternalFileIds?.length || 0}\n当前有效工作区会先保留恢复前备份。正在生成的回答会停止。`,
    buttons: ['取消', '恢复此备份'], defaultId: 0, cancelId: 0 })
  if (decision.response !== 1) return null
  if (workspaceBusy || quitting) throw new Error('本地工作区正在处理其他操作')
  workspaceBusy = true
  backendOrigin = null
  try {
    await backend?.stop()
    const result = await runWorkspace('restore', selected)
    await startBackend()
    return result
  } catch (error) {
    await showFailure(error)
    throw error
  } finally { workspaceBusy = false }
}

async function restartService() {
  if (workspaceBusy) throw new Error('正在处理工作区，请稍后重试')
  workspaceBusy = true
  backendOrigin = null
  try {
    await backend?.stop()
    await startBackend()
    return true
  } catch (error) { await showFailure(error); throw error }
  finally { workspaceBusy = false }
}

async function startBackend() {
  if (quitting) return
  startupFailure = null
  backend = new BackendProcess({ ...launchConfig, cwd: app.getPath('userData'), token: desktopToken, log, env: backendEnvironment })
  backend.on('unexpected-exit', ({ code, signal }) => showFailure(new Error(`本地服务意外退出（${code ?? signal}）`)))
  backendOrigin = await backend.start()
  if (quitting || startupFailure || !mainWindow) return
  await mainWindow.loadURL(`${backendOrigin}/`)
  mainWindow.show()
}

async function runSmoke() {
  const result = await mainWindow.webContents.executeJavaScript(`(async () => {
    async function waitFor(selector) {
      const deadline = Date.now() + 10000;
      while (!document.querySelector(selector)) {
        if (Date.now() > deadline) throw new Error('桌面界面未能完成加载：' + selector);
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      return document.querySelector(selector);
    }
    await waitFor('#app-root .dash');
    for (const [buttonText, dialogText] of [['分析任务', '可重复分析任务'], ['准确率评测', '准确率评测 · 真实标准样例']]) {
      const button = [...document.querySelectorAll('.topbar .actions button')].find(item => item.textContent.trim() === buttonText);
      if (!button) throw new Error('缺少分析入口：' + buttonText);
      button.click();
      const deadline = Date.now() + 10000;
      let opened;
      while (!(opened = [...document.querySelectorAll('.el-dialog__wrapper')].find(item => item.style.display !== 'none' && item.textContent.includes(dialogText)))) {
        if (Date.now() > deadline) throw new Error('分析窗口未打开：' + buttonText);
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      if (!opened.querySelector('.el-dialog__headerbtn')) throw new Error('分析窗口缺少关闭按钮');
      opened.querySelector('.el-dialog__headerbtn').click();
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    if (typeof window.require !== 'undefined' || typeof window.process !== 'undefined') {
      throw new Error('渲染进程隔离检查失败');
    }
    const session = await window.qaDesktop.bootstrap();
    const response = await fetch('/api/user/profile', {headers: {Authorization: 'Bearer ' + session.token}});
    const profile = await response.json();
    if (!response.ok || !session.user?.id || !(profile.user?.id || profile.id)) {
      throw new Error('桌面会话或用户资料检查失败');
    }
    (await waitFor('.session-panel .section-head button')).click();
    await waitFor('.msg-list');
    const sessionsResponse = await fetch('/api/sessions', {headers: {Authorization: 'Bearer ' + session.token}});
    const sessions = await sessionsResponse.json();
    if (!sessionsResponse.ok || !sessions.sessions?.length) throw new Error('创建会话未持久化');
    (await waitFor('button[aria-label="设置"]')).click();
    (await waitFor('#tab-desktop')).click();
    const localSettings = await waitFor('#pane-desktop');
    if (!localSettings.textContent.includes(session.version)) throw new Error('本地应用版本信息未显示');
    (await waitFor('.el-page-header__left')).click();
    (await waitFor('.dash .session-panel .item')).click();
    await new Promise(resolve => setTimeout(resolve, 0));
    await waitFor('.msg-list');
    const chat = document.querySelector('.chat-panel').__vue__;
    if (!chat || chat.$options.name !== 'ChatPanel') throw new Error('无法检查聊天渲染组件');
    // An upgraded profile can already select a different session while history
    // is loading. Wait for the current request before adding synthetic content.
    await chat.loadMessages();
    await chat.$nextTick();
    chat.messages.push({id: 'desktop-smoke-synthetic', role: 'assistant', status: 'completed',
      content: '**桌面渲染验证** <img src=x onerror="window.__qaDesktopSmokeXss=1"><a href="javascript:window.__qaDesktopSmokeXss=2" onclick="window.__qaDesktopSmokeXss=3">测试链接</a><script>window.__qaDesktopSmokeXss=4</script>'});
    await chat.$nextTick();
    const markdown = document.querySelector('.msg-list .assistant .md');
    if (!markdown?.querySelector('strong') || !markdown.textContent.includes('桌面渲染验证') ||
        markdown.querySelector('img, script, [onerror], [onclick], a[href^="javascript:"]') ||
        window.__qaDesktopSmokeXss !== undefined) throw new Error('聊天内容安全渲染检查失败');
    if (typeof window.qaDesktop.copyText !== 'function') throw new Error('桌面复制功能未加载');
    chat.messages.push({id: 'desktop-smoke-evidence', role: 'assistant', status: 'completed', content: '合成查询结果',
      source: {name: 'smoke-source', type: 'sqlite', file_path: '/synthetic/source.sqlite'},
      generation_snapshot: {model: 'smoke-model', api_url: 'https://user:secret@service.example/v1?key=hidden#fragment',
        dialect: 'sqlite', schema: 'CREATE TABLE smoke_source (total INTEGER)', business_context: '合成业务口径',
        history: [], question: '合成问题', task: null},
      evidence: {sql: 'SELECT 42 AS total', columns: ['total'], rows: [{total: 42}],
        coverage: {returned_rows: 1, analyzed_rows: 1, row_limit: 1000}}});
    await chat.$nextTick();
    const evidence = await waitFor('.query-evidence');
    evidence.open = true;
    if (!evidence.textContent.includes('SELECT 42 AS total') || !evidence.textContent.includes('smoke-source') || !evidence.textContent.includes('42')) throw new Error('查询证据未完整渲染');
    const snapshot = await waitFor('.query-evidence .generation-snapshot');
    snapshot.open = true;
    if (!snapshot.textContent.includes('smoke-model') || !snapshot.textContent.includes('合成业务口径') ||
        !snapshot.textContent.includes('CREATE TABLE smoke_source') || !snapshot.textContent.includes('service.example') ||
        ['user:secret', 'key=hidden', '#fragment', 'service.example/v1'].some(secret => snapshot.textContent.includes(secret))) {
      throw new Error('生成输入快照或 AI 地址隐私显示检查失败');
    }
    return {ok: true};
  })()`)
  if (!result.ok) throw new Error('桌面会话或用户资料检查失败')
  const backupFile = path.join(app.getPath('userData'), 'smoke-workspace.zip')
  await runWorkspace('backup', backupFile, true)
  const checked = await runWorkspace('inspect', backupFile)
  if (!checked.valid) throw new Error('真实后端备份验证失败')
  await backend.stop()
  await runWorkspace('restore', backupFile)
  await startBackend()
  const restored = await mainWindow.webContents.executeJavaScript(`(async () => {
    const session = await window.qaDesktop.bootstrap();
    const response = await fetch('/api/sessions', {headers: {Authorization: 'Bearer ' + session.token}});
    return response.ok && (await response.json()).sessions.length > 0;
  })()`)
  if (!restored) throw new Error('恢复后会话验证失败')
  await runWorkspace('diagnostics', path.join(app.getPath('userData'), 'smoke-diagnostics.zip'))
  finishSmoke(true)
}

async function startApplication() {
  const userData = app.getPath('userData')
  const dataDir = path.join(userData, 'data')
  const logsDir = path.join(userData, 'logs')
  fs.mkdirSync(dataDir, { recursive: true, mode: 0o700 })
  fs.mkdirSync(logsDir, { recursive: true, mode: 0o700 })
  logStream = new RotatingLog(path.join(logsDir, 'desktop.log'))
  log('desktop', `启动 ${app.getVersion()} (${process.platform}/${process.arch})`)
  installSessionSecurity()
  registerIpc()
  installRecoveryMenu()
  createWindow()
  await mainWindow.loadURL(statusPage('正在启动智能数据问答', '正在准备本地数据和服务，首次启动可能需要片刻。'))
  if (quitting) return
  const root = path.resolve(__dirname, '..')
  launchConfig = backendLaunch({ packaged: app.isPackaged, platform: process.platform,
    resourcesPath: process.resourcesPath, root, pythonOverride: process.env.QA_BACKEND_PYTHON })
  backendEnvironment = {
      ...process.env,
      PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1',
      QA_DESKTOP_MODE: '1', QA_DATA_DIR: dataDir, QA_DESKTOP_TOKEN: desktopToken,
      QA_WEB_DIR: app.isPackaged ? path.join(process.resourcesPath, 'web') : path.join(root, 'frontend', 'dist'),
  }
  await startBackend()
  if (smokeMode) await runSmoke()
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.show()
      mainWindow.focus()
    } else if (desktopSession) app.emit('activate')
  })
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin' || smokeMode) app.quit()
  })
  app.on('activate', () => {
    if (mainWindow || !desktopSession || quitting) return
    createWindow()
    if (startupFailure) showFailure(startupFailure)
    else mainWindow.loadURL(backendOrigin ? `${backendOrigin}/` : statusPage('正在启动智能数据问答', '正在准备本地服务。')).catch(showFailure)
  })
  app.on('before-quit', event => {
    if (allowQuit) return
    event.preventDefault()
    if (quitting) return
    quitting = true
    Promise.resolve(backend?.stop()).then(() => Promise.allSettled([...activeWorkspaceCommands])).finally(async () => {
      log('desktop', '应用退出')
      if (logStream) await new Promise(resolve => logStream.end(resolve))
      allowQuit = true
      if (smokeExitCode !== null) app.exit(smokeExitCode)
      else app.quit()
    })
  })
  app.whenReady().then(startApplication).catch(async error => {
    await backend?.stop()
    await showFailure(error)
  })
}
