<template>
  <div class="settings">
    <div class="bar">
      <el-page-header content="设置" @back="$router.push('/')" />
    </div>

    <el-card class="card">
      <el-tabs v-model="tab">
        <!-- 系统配置 -->
        <el-tab-pane label="AI 服务配置" name="system">
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
            <el-form-item>
              <el-button type="primary" :loading="savingSys" @click="saveSystem">保存配置</el-button>
              <el-button :loading="testingAi" @click="testAi">测试连接</el-button>
            </el-form-item>
            <div v-if="aiMsg" :class="['test-msg', aiOk ? 'ok' : 'fail']">{{ aiMsg }}</div>
          </el-form>
        </el-tab-pane>

        <!-- 个人信息 -->
        <el-tab-pane label="个人信息" name="profile">
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
        <el-tab-pane label="修改密码" name="password">
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
      </el-tabs>
    </el-card>
  </div>
</template>

<script>
import * as api from '../api'

export default {
  name: 'SettingsView',
  data() {
    return {
      tab: 'system',
      sys: { api_key: '', api_url: '', timeout: 30, api_key_set: false },
      profile: { username: '', phone: '' },
      pwd: { old_password: '', new_password: '', confirm: '' },
      savingSys: false, testingAi: false, savingProfile: false, savingPwd: false,
      aiMsg: '', aiOk: false,
    }
  },
  async mounted() {
    const cfg = (await api.getSystemConfig()).config
    this.sys = { api_key: '', api_url: cfg.api_url, timeout: cfg.timeout, api_key_set: cfg.api_key_set }
    const u = (await api.getProfile()).user
    this.profile = { username: u.username, phone: u.phone }
  },
  methods: {
    async saveSystem() {
      this.savingSys = true
      try {
        const body = { api_url: this.sys.api_url, timeout: this.sys.timeout }
        if (this.sys.api_key) body.api_key = this.sys.api_key
        await api.updateSystemConfig(body)
        this.$message.success('配置更新成功')
        this.sys.api_key = ''
        this.sys.api_key_set = true
      } catch (e) { /* handled */ } finally { this.savingSys = false }
    },
    async testAi() {
      this.testingAi = true; this.aiMsg = ''
      try {
        const res = await api.testAi()
        this.aiOk = res.success; this.aiMsg = res.message
      } catch (e) { this.aiOk = false; this.aiMsg = e.message } finally { this.testingAi = false }
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
</style>
