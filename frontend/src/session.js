// Some in-app browsers block persistent storage. Keep checkout usable in memory.
export function readSessionToken() {
  try { return localStorage.getItem('omnicart-session') || '' } catch { return '' }
}
export function saveSessionToken(token) {
  try { localStorage.setItem('omnicart-session', token); return true } catch { return false }
}
export function clearSessionToken() {
  try { localStorage.removeItem('omnicart-session') } catch { /* Current in-memory session is still cleared. */ }
}
