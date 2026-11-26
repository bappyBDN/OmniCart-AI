import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Chat, { ProductCards, SourceList } from './Chat'

afterEach(cleanup)

describe('source transparency', () => {
  it('shows the original policy and its source ID', () => {
    render(<SourceList sources={[{ id: 'policy:1:0', kind: 'policy', title: 'Refund Policy', content: 'Refunds within 7 days.' }]} />)
    expect(screen.getByText('1 retrieved source')).toBeInTheDocument()
    expect(screen.getByText('Refunds within 7 days.')).toBeInTheDocument()
    expect(screen.getByText('policy · policy:1:0')).toBeInTheDocument()
  })
  it('shows labelled similar-product images and stock', () => {
    render(<ProductCards sources={[{ id: 'product:SKU_101', kind: 'product', title: 'Cotton shirt', graph: { relation: 'alternative' }, data: { name: 'Cotton shirt', category: 'Apparel', effective_price: 19.99, stock_count: 3, image_url: '/api/products/SKU_101/image' } }]} />)
    expect(screen.getByAltText('Cotton shirt product image')).toBeInTheDocument()
    expect(screen.getByText('Similar in-stock alternative')).toBeInTheDocument()
    expect(screen.getByText('৳19.99 · 3 in stock')).toBeInTheDocument()
  })
  it('renders live product price and stock instead of raw JSON', () => {
    render(<SourceList sources={[{ id: 'product:SKU_101', kind: 'product', title: 'Cotton shirt', data: { effective_price: 19.99, stock_count: 3, description: 'Cotton.' } }]} />)
    expect(screen.getByText('৳19.99 · 3 in stock · Cotton.')).toBeInTheDocument()
  })
})

describe('conversation checkout continuity', () => {
  it('keeps a pending draft visible after an unrelated policy follow-up', async () => {
    const draft = { draft_id: 'draft-1', product_name: 'Protein Powder', quantity: 2, total_price: 89.98 }
    const profile = { name: 'Test shopper', human_intervention: false, missing: [] }
    let sent = false
    const request = vi.fn(async path => {
      if (path === '/profile') return profile
      if (path === '/checkout') return [draft]
      if (path === '/messages') return sent ? [{ id: 1, sender: 'ai', content: 'Delivery depends on the address zone.' }] : []
      if (path === '/chat') {
        sent = true
        return { answer: 'Delivery depends on the address zone.', drafts: [], sources: [], trace: [], route: 'support', usage: {}, latency_ms: 0 }
      }
      return []
    })
    render(<Chat request={request} health={{ dense_status: 'ready' }} profile={profile} setProfile={vi.fn()} notify={vi.fn()} onCheckout={vi.fn()} />)
    await screen.findByRole('button', { name: /Review 2 × Protein Powder/ })
    fireEvent.change(screen.getByLabelText('Message OmniCart'), { target: { value: 'What about delivery?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    await screen.findByText('Delivery depends on the address zone.')
    await waitFor(() => expect(screen.getByRole('button', { name: /Review 2 × Protein Powder/ })).toBeInTheDocument())
  })
})
