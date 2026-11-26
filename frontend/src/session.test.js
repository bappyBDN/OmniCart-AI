import { afterEach, expect, it, vi } from 'vitest'
import { clearSessionToken, readSessionToken, saveSessionToken } from './session'

afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

it('stores only the private session token', () => {
  expect(saveSessionToken('test-session')).toBe(true)
  expect(readSessionToken()).toBe('test-session')
  clearSessionToken()
  expect(readSessionToken()).toBe('')
})

it('does not crash when an in-app browser blocks persistent storage', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('Blocked', 'SecurityError') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Blocked', 'SecurityError') })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('Blocked', 'SecurityError') })
  expect(readSessionToken()).toBe('')
  expect(saveSessionToken('test-session')).toBe(false)
  expect(() => clearSessionToken()).not.toThrow()
})
