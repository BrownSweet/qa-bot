// The configured endpoint may contain credentials in its userinfo, path or query.
// Showing only the origin is enough to identify the provider without exposing them.
export function providerAddressForDisplay(value) {
  if (!value) return '未记录'
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol)) return '地址已隐藏'
    return `${url.protocol}//${url.host}${url.pathname && url.pathname !== '/' ? '/…' : ''}`
  } catch {
    return '地址已隐藏'
  }
}

export function providerIdentityForDisplay(snapshot) {
  if (snapshot?.api_url) return providerAddressForDisplay(snapshot.api_url)
  const fingerprint = snapshot?.api_url_sha256
  return typeof fingerprint === 'string' && /^[a-f0-9]{64}$/i.test(fingerprint)
    ? `SHA-256 ${fingerprint.slice(0, 12).toLowerCase()}…`
    : '未记录'
}

export function snapshotParameters(snapshot) {
  const parameters = snapshot?.task?.parameters
  if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) return []
  return Object.keys(parameters).sort().map(name => ({ name, value: String(parameters[name]) }))
}
