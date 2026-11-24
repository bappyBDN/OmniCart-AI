import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, currency } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('API client', () => {
  it('sends the session token and JSON body', async () => {
    const mock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ answer: 'hello' }) })
    vi.stubGlobal('fetch', mock)
    expect(await api('/chat', { token: 'secret', method: 'POST', body: { message: 'hi' } })).toEqual({ answer: 'hello' })
    expect(mock.mock.calls[0][1].headers.Authorization).toBe('Bearer secret')
    expect(JSON.parse(mock.mock.calls[0][1].body).message).toBe('hi')
  })
  it('formats validation errors without losing the field', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: [{ loc: ['body', 'quantity'], msg: 'Must be positive' }] }) }))
    await expect(api('/checkout')).rejects.toThrow('quantity: Must be positive')
  })
  it('handles session deletion with no response body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))
    expect(await api('/sessions/current', { method: 'DELETE' })).toBeNull()
  })
  it('formats monetary amounts', () => { expect(currency(19.99)).toBe('$19.99') })
})
