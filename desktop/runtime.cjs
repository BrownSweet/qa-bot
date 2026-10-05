'use strict'

const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { EventEmitter } = require('node:events')
const { spawn } = require('node:child_process')

const READY_PREFIX = 'QA_DESKTOP_READY '

class RotatingLog {
  constructor(filename, { maxBytes = 4 * 1024 * 1024, count = 3 } = {}) {
    Object.assign(this, { filename, maxBytes, count })
  }
  write(message) {
    const content = String(message).slice(0, 65536)
    if (fs.existsSync(this.filename) && fs.statSync(this.filename).size + Buffer.byteLength(content) > this.maxBytes) {
      for (let index = this.count - 1; index >= 1; index--) {
        const older = `${this.filename}.${index}`
        if (fs.existsSync(older)) {
          if (index === this.count - 1) fs.unlinkSync(older)
          else fs.renameSync(older, `${this.filename}.${index + 1}`)
        }
      }
      fs.renameSync(this.filename, `${this.filename}.1`)
    }
    fs.appendFileSync(this.filename, content, { mode: 0o600 })
  }
  end(callback) { callback?.() }
}

function workspaceCommand(launch, options, { spawnFn = spawn, timeoutMs = 90000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawnFn(launch.command, [...launch.args, '--workspace', options.operation, '--file', options.file, '--data-dir', options.dataDir, ...(options.includeSources ? ['--include-sources'] : [])],
      { cwd: options.cwd, env: options.env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
    let output = '', diagnostic = ''
    const timeout = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('工作区操作超时，原数据未主动删除，请查看日志')) }, timeoutMs)
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', chunk => { output += chunk; if (output.length > 2 * 1024 * 1024) child.kill('SIGKILL') })
    child.stderr.on('data', chunk => { diagnostic = (diagnostic + chunk).slice(-4000) })
    child.once('error', error => { clearTimeout(timeout); reject(error) })
    child.once('close', code => {
      clearTimeout(timeout)
      const line = output.split('\n').find(value => value.startsWith('QA_WORKSPACE_RESULT '))
      try {
        const result = line && JSON.parse(line.slice('QA_WORKSPACE_RESULT '.length))
        if (code !== 0 || !result || result.error) {
          options.log?.('workspace', result?.error || diagnostic || '工作区命令未正常完成')
          throw new Error(result?.error || '工作区操作失败，请查看日志')
        }
        resolve(result)
      } catch (error) { reject(error) }
    })
  })
}

function backendLaunch({ packaged, platform, resourcesPath, root, pythonOverride,
  exists = fs.existsSync, readFile = fs.readFileSync }) {
  if (packaged) {
    const directory = path.join(resourcesPath, 'backend')
    const infoPath = path.join(directory, 'build-info.json')
    if (platform === 'win32' && exists(infoPath)) {
      const info = JSON.parse(readFile(infoPath, 'utf8'))
      if (info.runtime === 'python-embedded') {
        return { command: path.join(directory, 'python.exe'), args: [path.join(directory, 'desktop_server.py')] }
      }
    }
    return { command: path.join(directory, platform === 'win32' ? 'qa-backend.exe' : 'qa-backend'), args: [] }
  }
  const bundledPython = path.join(root, 'backend', '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  return {
    command: pythonOverride || (exists(bundledPython) ? bundledPython : platform === 'win32' ? 'python' : 'python3'),
    args: [path.join(root, 'backend', 'desktop_server.py')],
  }
}

function isTrustedUrl(value, origin) {
  if (!origin) return false
  try {
    const url = new URL(value)
    return url.origin === origin && !url.username && !url.password
  } catch {
    return false
  }
}

function isTrustedSender(event, window, origin) {
  return Boolean(window && !window.isDestroyed() &&
    event.sender === window.webContents &&
    event.senderFrame === window.webContents.mainFrame &&
    isTrustedUrl(event.senderFrame?.url, origin))
}

function clipboardHandler({ assertSender, writeText }) {
  return (event, value) => {
    assertSender(event)
    if (typeof value !== 'string' || Buffer.byteLength(value, 'utf8') > 2 * 1024 * 1024) {
      throw new Error('复制内容必须是最多 2 MiB 的文本')
    }
    writeText(value)
    return true
  }
}

function desktopHeaders(details, origin, token, webContentsId) {
  const headers = { ...details.requestHeaders }
  // A redirect must never carry the private header to another origin.
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === 'x-desktop-token') delete headers[key]
  }
  if (details.webContentsId !== webContentsId || !isTrustedUrl(details.url, origin)) return headers
  if (!isTrustedUrl(details.frame?.url, origin)) return headers
  const pathname = new URL(details.url).pathname
  if (pathname === '/health' || pathname === '/api' || pathname.startsWith('/api/')) {
    headers['X-Desktop-Token'] = token
  }
  return headers
}

function requestJson(origin, pathname, token, { timeoutMs = 5000 } = {}) {
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin) || !pathname.startsWith('/') || pathname.startsWith('//')) {
    return Promise.reject(new Error('无效的本地服务地址'))
  }
  return new Promise((resolve, reject) => {
    const request = http.get(`${origin}${pathname}`, {
      headers: { 'X-Desktop-Token': token, Accept: 'application/json' },
    }, response => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', chunk => {
        body += chunk
        if (body.length > 1024 * 1024) request.destroy(new Error('本地服务响应过大'))
      })
      response.on('error', reject)
      response.on('end', () => {
        if (response.statusCode !== 200) {
          reject(new Error(`本地服务返回 HTTP ${response.statusCode}`))
          return
        }
        try { resolve(JSON.parse(body)) } catch { reject(new Error('本地服务返回了无效 JSON')) }
      })
    })
    // Node http does not follow redirects, so the private header stays local.
    request.setTimeout(timeoutMs, () => request.destroy(new Error('本地服务请求超时')))
    request.on('error', reject)
  })
}

class BackendProcess extends EventEmitter {
  constructor({ command, args = [], cwd, env, token, log = () => {}, spawnFn = spawn,
    requestFn = requestJson, readyTimeoutMs = 45000, gracefulTimeoutMs = 7000, killTimeoutMs = 1500 }) {
    super()
    Object.assign(this, { command, args, cwd, env, token, log, spawnFn, requestFn,
      readyTimeoutMs, gracefulTimeoutMs, killTimeoutMs })
    this.child = null
    this.origin = null
    this.stopping = false
    this.exited = false
  }

  start() {
    if (this.startPromise) return this.startPromise
    this.startPromise = new Promise((resolve, reject) => {
      let buffer = ''
      let receivedReady = false
      let settled = false
      const finish = (error, origin) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        this.cancelStart = null
        if (error) reject(error)
        else {
          this.origin = origin
          resolve(origin)
        }
      }
      const timer = setTimeout(() => finish(new Error('本地服务启动超时，请查看日志')), this.readyTimeoutMs)
      this.cancelStart = () => finish(new Error('本地服务启动已取消'))
      try {
        this.child = this.spawnFn(this.command, this.args, {
          cwd: this.cwd, env: this.env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
        })
      } catch (error) {
        finish(error)
        return
      }
      const child = this.child
      child.stdin.on('error', () => {}) // EPIPE is expected if the backend exits first.
      child.stdout.setEncoding('utf8')
      child.stderr.setEncoding('utf8')
      child.stderr.on('data', chunk => this.log('backend', chunk))
      child.stdout.on('data', chunk => {
        this.log('backend', chunk)
        if (settled || receivedReady || this.stopping) return
        buffer += chunk
        // Do not let malformed/unbounded startup output exhaust the desktop process.
        if (buffer.length > 65536) {
          finish(new Error('本地服务启动输出异常'))
          return
        }
        let newline
        while ((newline = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newline).trimEnd()
          buffer = buffer.slice(newline + 1)
          if (line.startsWith('QA_DESKTOP_ERROR ')) {
            try { finish(new Error(JSON.parse(line.slice('QA_DESKTOP_ERROR '.length)).message || '本地服务启动失败')) }
            catch { finish(new Error('本地服务启动失败，请查看日志')) }
            return
          }
          if (!line.startsWith(READY_PREFIX)) continue
          let port
          try { ({ port } = JSON.parse(line.slice(READY_PREFIX.length))) } catch {
            finish(new Error('本地服务就绪消息无效'))
            return
          }
          if (!Number.isInteger(port) || port < 1 || port > 65535) {
            finish(new Error('本地服务端口无效'))
            return
          }
          receivedReady = true
          const origin = `http://127.0.0.1:${port}`
          this.requestFn(origin, '/health', this.token).then(health => {
            if (health?.status !== 'ready') finish(new Error('本地服务尚未就绪'))
            else finish(null, origin)
          }, finish)
          return
        }
      })
      child.on('error', error => {
        if (!child.pid) this.exited = true
        this.log('desktop', `无法启动本地服务：${error.message}\n`)
        finish(new Error(`无法启动本地服务：${error.message}`))
      })
      child.on('exit', (code, signal) => {
        this.exited = true
        this.log('desktop', `本地服务退出 code=${code} signal=${signal}\n`)
        finish(new Error(`本地服务在启动时退出（${code ?? signal}）`))
        if (this.origin && !this.stopping) this.emit('unexpected-exit', { code, signal })
      })
    })
    return this.startPromise
  }

  stop() {
    if (this.stopPromise) return this.stopPromise
    this.stopping = true
    this.cancelStart?.()
    this.stopPromise = new Promise(resolve => {
      const child = this.child
      if (!child || this.exited || child.exitCode !== null || child.signalCode !== null) {
        resolve()
        return
      }
      let termTimer, killTimer, finalTimer
      const finish = () => {
        clearTimeout(termTimer)
        clearTimeout(killTimer)
        clearTimeout(finalTimer)
        child.removeListener('exit', finish)
        child.removeListener('close', finish)
        resolve()
      }
      child.once('exit', finish)
      child.once('close', finish)
      termTimer = setTimeout(() => {
        killTimer = setTimeout(() => {
          finalTimer = setTimeout(finish, 1000)
          child.kill('SIGKILL')
        }, this.killTimeoutMs)
        child.kill('SIGTERM')
      }, this.gracefulTimeoutMs)
      // The Python entry point treats stdin EOF as a graceful shutdown request.
      if (child.stdin.writable) child.stdin.end()
    })
    return this.stopPromise
  }
}

module.exports = { BackendProcess, backendLaunch, clipboardHandler, desktopHeaders, isTrustedSender, isTrustedUrl, requestJson, RotatingLog, workspaceCommand }
