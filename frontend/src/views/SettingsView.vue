<template>
  <div class="settings">
    <div class="bar">
      <el-page-header content="设置" @back="$router.push('/')" />
    </div>

    <el-alert v-if="settingsError" :title="settingsError" type="error" :closable="false" />
    <el-card class="card">
      <el-tabs v-model="tab">
        <!-- 系统配置 -->
        <el-tab-pane v-if="canManageAi" label="AI 服务配置" name="system">
          <el-form :model="sys" label-width="120px" style="max-width: 520px">
            <el-form-item label="API Key">
              <el-input v-model="sys.api_key" type="password" show-password
                        :placeholder="sys.api_key_set ? '已配置（如需修改请重新输入）' : 'sk-...'" />
            </el-form-item>
            <el-form-item label="API 地址">
              <el-input v-model="sys.api_url" placeholder="https://api.deepseek.com" />
            </el-form-item>
            <el-form-item label="超时时间(秒)">
              <el-input-number v-model="sys.timeout" :min="1" :max="60" />
            </el-form-item>
            <el-form-item label="模型名称"><el-input v-model="sys.model" placeholder="例如 deepseek-chat" /></el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingSys" :disabled="testingAi" @click="saveSystem">保存配置</el-button>
              <el-button :loading="testingAi" :disabled="savingSys" @click="testAi">保存并测试连接</el-button>
            </el-form-item>
            <p v-if="isDesktop" class="local-note">会话和配置保存在本机。AI 分析需联网，会将问题、表结构及查询结果发送到你配置的 AI 服务。</p>
            <div v-if="aiMsg" :class="['test-msg', aiOk ? 'ok' : 'fail']">{{ aiMsg }}</div>
          </el-form>
        </el-tab-pane>

        <!-- 个人信息 -->
        <el-tab-pane v-if="!isDesktop" label="个人信息" name="profile">
          <el-form :model="profile" label-width="120px" style="max-width: 520px">
            <el-form-item label="用户名">
              <el-input v-model="profile.username" />
            </el-form-item>
            <el-form-item label="手机号">
              <el-input v-model="profile.phone" disabled />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingProfile" @click="saveProfile">保存</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- 修改密码 -->
        <el-tab-pane v-if="!isDesktop" label="修改密码" name="password">
          <el-form :model="pwd" label-width="120px" style="max-width: 520px">
            <el-form-item label="旧密码">
              <el-input v-model="pwd.old_password" type="password" show-password />
            </el-form-item>
            <el-form-item label="新密码">
              <el-input v-model="pwd.new_password" type="password" show-password />
            </el-form-item>
            <el-form-item label="确认新密码">
              <el-input v-model="pwd.confirm" type="password" show-password />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="savingPwd" @click="changePwd">确认修改</el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane v-if="isDesktop" label="本地应用" name="desktop">
          <p>问答机器人 · 本地版</p>
          <p class="local-note">版本 {{ desktopInfo.version || '—' }} · {{ platformName }}</p>
          <p class="local-note">本地版无需注册或登录。备份保存本机配置与会话，可在原生窗口选择是否携带 Excel / SQLite 文件。备份包含敏感配置，请妥善保存。</p>
          <div class="maintenance-actions">
            <el-button size="small" icon="el-icon-folder-opened" @click="openDataDirectory">打开数据目录</el-button>
            <el-button size="small" @click="maintenance('openLogsDirectory')">打开日志目录</el-button>
            <el-button size="small" :disabled="!!maintenanceAction" @click="maintenance('backup')">创建备份</el-button>
            <el-button size="small" :disabled="!!maintenanceAction" @click="maintenance('inspectBackup')">检查备份</el-button>
            <el-button size="small" :disabled="!!maintenanceAction" @click="maintenance('restoreBackup')">从备份恢复</el-button>
            <el-button size="small" :disabled="!!maintenanceAction" @click="maintenance('exportDiagnostics')">导出脱敏诊断</el-button>
            <el-button size="small" :disabled="!!maintenanceAction" @click="maintenance('restartBackend')">重启本地服务</el-button>
          </div>
          <p class="local-note">恢复备份会替换当前本地数据，并在原生窗口再次确认。重启服务会停止正在生成的回答并重新载入应用。</p>
          <p v-if="maintenanceAction" role="status" class="local-note"><i class="el-icon-loading"></i> 正在处理，请完成原生窗口中的操作…</p>
          <el-alert v-if="maintenanceMessage" :title="maintenanceMessage" :type="maintenanceError ? 'error' : 'success'" :closable="false" />
          <div v-if="backupInfo" class="local-note">
            <p>备份有效 · 版本 {{ backupInfo.version }} · {{ backupInfo.createdAt }}</p>
            <p v-if="backupInfo.message">{{ backupInfo.message }}</p>
            <p v-if="backupInfo.missingExternalFileIds && backupInfo.missingExternalFileIds.length">有 {{ backupInfo.missingExternalFileIds.length }} 个外部文件当前不可用；恢复后请在数据源中重新选择文件。</p>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script>
import * as api from '../api'
import { desktop, isDesktop } from '../desktop'

export default {
  name: 'SettingsView',
  data() {
    return {
      tab: 'system',
      isDesktop,
      canManageAi: isDesktop, settingsError: '',
      sys: { api_key: '', api_url: '', model: '', timeout: 30, api_key_set: false },
      profile: { username: '', phone: '' },
      pwd: { old_password: '', new_password: '', confirm: '' },
      savingSys: false, testingAi: false, savingProfile: false, savingPwd: false,
      aiMsg: '', aiOk: false,
      maintenanceAction: '', maintenanceMessage: '', maintenanceError: false, backupInfo: null,
    }
  },
  computed: {
    desktopInfo() { return this.$store.state.desktop || {} },
    platformName() {
      return ({ darwin: 'macOS', win32: 'Windows', linux: 'Linux' })[this.desktopInfo.platform] || this.desktopInfo.platform || '本地应用'
    },
  },
  async mounted() {
    const profileLoad = this.isDesktop ? Promise.resolve() : this.loadProfile()
    await Promise.allSettled([this.loadPermissions(), profileLoad])
  },
  methods: {
    async loadPermissions() {
      try {
        const state = await api.getReadiness()
        this.canManageAi = !!state.can_manage_ai
        this.tab = this.canManageAi ? 'system' : (this.isDesktop ? 'desktop' : 'profile')
        if (this.$route && this.$route.query.tab === 'desktop' && this.isDesktop) this.tab = 'desktop'
        if (this.canManageAi) await this.loadSystem()
      } catch (error) { this.settingsError = error.message || '无法加载配置权限'; if (!this.isDesktop) this.tab = 'profile' }
    },
    async loadProfile() {
      try { const user = (await api.getProfile()).user; this.profile = { username: user.username, phone: user.phone } }
      catch (error) { this.settingsError = error.message || '个人信息加载失败' }
    },
    async loadSystem() {
      const cfg = (await api.getSystemConfig()).config
      this.sys = { api_key: '', api_url: cfg.api_url, model: cfg.model, timeout: cfg.timeout, api_key_set: cfg.api_key_set }
    },
    async persistSystem() {
      const body = { api_url: this.sys.api_url, model: this.sys.model, timeout: this.sys.timeout }
      if (this.sys.api_key) body.api_key = this.sys.api_key
      await api.updateSystemConfig(body)
      await this.loadSystem()
    },
    async saveSystem() {
      this.savingSys = true
      try {
        await this.persistSystem()
        this.$message.success('配置更新成功')
      } catch (e) { /* handled */ } finally { this.savingSys = false }
    },
    async testAi() {
      this.testingAi = true; this.aiMsg = ''
      try {
        await this.persistSystem()
        const res = await api.testAi()
        this.aiOk = res.success; this.aiMsg = res.message
      } catch (e) { this.aiOk = false; this.aiMsg = e.message } finally { this.testingAi = false }
    },
    async openDataDirectory() {
      try { await desktop.openDataDirectory() }
      catch (e) { this.$message.error(e.message || '无法打开数据目录') }
    },
    async maintenance(action) {
      if (this.maintenanceAction) return
      if (action === 'restartBackend') {
        try { await this.$confirm('重启将停止正在生成的回答，并重新载入本地应用。是否继续？', '重启本地服务', { type: 'warning' }) }
        catch { return }
      }
      this.maintenanceAction = action; this.maintenanceMessage = ''; this.maintenanceError = false; this.backupInfo = null
      try {
        const result = await desktop[action]()
        if (result === null) return
        if (action === 'backup' || action === 'inspectBackup') this.backupInfo = result
        this.maintenanceMessage = ({ backup: '备份已保存', inspectBackup: '备份检查通过', restoreBackup: '恢复完成，正在重新载入',
          exportDiagnostics: '脱敏诊断已导出', restartBackend: '本地服务正在重新启动', openLogsDirectory: '日志目录已打开' })[action]
        if (result && result.path) this.maintenanceMessage += `：${result.path}`
      } catch (error) { this.maintenanceError = true; this.maintenanceMessage = error.message || '本地操作失败' }
      finally { this.maintenanceAction = '' }
    },
    async saveProfile() {
      this.savingProfile = true
      try {
        await api.updateProfile({ username: this.profile.username })
        this.$message.success('个人信息更新成功')
        const user = { ...this.$store.state.user, username: this.profile.username }
        this.$store.commit('setUser', user)
      } catch (e) { /* handled */ } finally { this.savingProfile = false }
    },
    async changePwd() {
      if (this.pwd.new_password !== this.pwd.confirm) return this.$message.warning('两次输入的新密码不一致')
      this.savingPwd = true
      try {
        await api.changePassword({ old_password: this.pwd.old_password, new_password: this.pwd.new_password })
        this.$message.success('密码修改成功')
        this.pwd = { old_password: '', new_password: '', confirm: '' }
      } catch (e) { /* handled */ } finally { this.savingPwd = false }
    },
  },
}
</script>

<style scoped>
.settings { padding: 20px; max-width: 900px; margin: 0 auto; }
.bar { margin-bottom: 16px; }
.test-msg { padding: 6px 10px; border-radius: 4px; font-size: 13px; display: inline-block; }
.test-msg.ok { background: #f0f9eb; color: #67c23a; }
.test-msg.fail { background: #fef0f0; color: #f56c6c; }
.local-note { font-size: 13px; color: #606266; line-height: 1.7; }
.maintenance-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.maintenance-actions .el-button { margin: 0; }
</style>
