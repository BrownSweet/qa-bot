<template>
  <div class="auth-wrap">
    <el-card class="auth-card">
      <h2 class="title">问答机器人</h2>

      <!-- 登录 -->
      <el-form v-if="mode === 'login'" :model="loginForm" ref="loginForm" :rules="rules" @submit.native.prevent>
        <el-form-item prop="username">
          <el-input v-model="loginForm.username" placeholder="用户名" prefix-icon="el-icon-user" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="loginForm.password" type="password" placeholder="密码" prefix-icon="el-icon-lock"
                    show-password @keyup.enter.native="doLogin" />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="loginForm.remember_me">记住我</el-checkbox>
          <el-link type="primary" class="right-link" @click="mode = 'forgot'">忘记密码？</el-link>
        </el-form-item>
        <el-button type="primary" :loading="loading" class="full" @click="doLogin">登录</el-button>
        <div class="switch">还没有账号？<el-link type="primary" @click="mode = 'register'">立即注册</el-link></div>
      </el-form>

      <!-- 注册 -->
      <el-form v-else-if="mode === 'register'" :model="registerForm" ref="registerForm" :rules="rules" @submit.native.prevent>
        <el-form-item prop="username">
          <el-input v-model="registerForm.username" placeholder="用户名 (3-20字符)" prefix-icon="el-icon-user" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="registerForm.password" type="password" placeholder="密码 (6-32字符)"
                    prefix-icon="el-icon-lock" show-password />
        </el-form-item>
        <el-form-item prop="phone">
          <el-input v-model="registerForm.phone" placeholder="手机号" prefix-icon="el-icon-mobile-phone" />
        </el-form-item>
        <el-form-item prop="code">
          <div class="code-row">
            <el-input v-model="registerForm.code" placeholder="验证码" />
            <el-button :disabled="countdown > 0" @click="sendCodeFor('register', registerForm.phone)">
              {{ countdown > 0 ? countdown + 's' : '获取验证码' }}
            </el-button>
          </div>
        </el-form-item>
        <el-button type="primary" :loading="loading" class="full" @click="doRegister">注册</el-button>
        <div class="switch">已有账号？<el-link type="primary" @click="mode = 'login'">返回登录</el-link></div>
      </el-form>

      <!-- 找回密码 -->
      <el-form v-else :model="forgotForm" ref="forgotForm" :rules="rules" @submit.native.prevent>
        <el-form-item prop="phone">
          <el-input v-model="forgotForm.phone" placeholder="手机号" prefix-icon="el-icon-mobile-phone" />
        </el-form-item>
        <el-form-item prop="code">
          <div class="code-row">
            <el-input v-model="forgotForm.code" placeholder="验证码" />
            <el-button :disabled="countdown > 0" @click="sendCodeFor('forgot', forgotForm.phone)">
              {{ countdown > 0 ? countdown + 's' : '获取验证码' }}
            </el-button>
          </div>
        </el-form-item>
        <el-form-item prop="new_password">
          <el-input v-model="forgotForm.new_password" type="password" placeholder="新密码 (6-32字符)"
                    prefix-icon="el-icon-lock" show-password />
        </el-form-item>
        <el-button type="primary" :loading="loading" class="full" @click="doForgot">重置密码</el-button>
        <div class="switch"><el-link type="primary" @click="mode = 'login'">返回登录</el-link></div>
      </el-form>
    </el-card>
  </div>
</template>

<script>
import * as api from '../api'

export default {
  name: 'AuthView',
  data() {
    return {
      mode: 'login',
      loading: false,
      countdown: 0,
      loginForm: { username: '', password: '', remember_me: false },
      registerForm: { username: '', password: '', phone: '', code: '' },
      forgotForm: { phone: '', code: '', new_password: '' },
      rules: {
        username: [{ required: true, min: 3, max: 20, message: '用户名为3-20字符', trigger: 'blur' }],
        password: [{ required: true, min: 6, max: 32, message: '密码为6-32字符', trigger: 'blur' }],
        new_password: [{ required: true, min: 6, max: 32, message: '密码为6-32字符', trigger: 'blur' }],
        phone: [{ required: true, pattern: /^1\d{10}$/, message: '请输入11位手机号', trigger: 'blur' }],
        code: [{ required: true, len: 6, message: '请输入6位验证码', trigger: 'blur' }],
      },
    }
  },
  methods: {
    async sendCodeFor(type, phone) {
      if (!/^1\d{10}$/.test(phone)) return this.$message.warning('请输入正确的手机号')
      try {
        const res = await api.sendCode({ phone, type })
        this.$message.success(res.dev_code ? `验证码已发送（开发模式: ${res.dev_code}）` : '验证码已发送')
        this.startCountdown()
      } catch (e) { /* 已由拦截器提示 */ }
    },
    startCountdown() {
      this.countdown = 60
      const t = setInterval(() => { if (--this.countdown <= 0) clearInterval(t) }, 1000)
    },
    doLogin() {
      this.$refs.loginForm.validate(async (ok) => {
        if (!ok) return
        this.loading = true
        try {
          const res = await api.login(this.loginForm)
          this.$store.dispatch('loginSuccess', { token: res.token, user: res.user })
          this.$message.success('登录成功')
          this.$router.replace('/')
        } catch (e) { /* handled */ } finally { this.loading = false }
      })
    },
    doRegister() {
      this.$refs.registerForm.validate(async (ok) => {
        if (!ok) return
        this.loading = true
        try {
          await api.register(this.registerForm)
          this.$message.success('注册成功，请登录')
          this.mode = 'login'
        } catch (e) { /* handled */ } finally { this.loading = false }
      })
    },
    doForgot() {
      this.$refs.forgotForm.validate(async (ok) => {
        if (!ok) return
        this.loading = true
        try {
          await api.forgotPassword(this.forgotForm)
          this.$message.success('密码重置成功，请登录')
          this.mode = 'login'
        } catch (e) { /* handled */ } finally { this.loading = false }
      })
    },
  },
}
</script>

<style scoped>
.auth-wrap { height: 100%; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #667eea, #764ba2); }
.auth-card { width: 380px; }
.title { text-align: center; margin: 0 0 24px; color: #303133; }
.full { width: 100%; }
.switch { text-align: center; margin-top: 16px; font-size: 13px; color: #909399; }
.right-link { float: right; }
.code-row { display: flex; gap: 8px; }
.code-row .el-button { white-space: nowrap; }
</style>
