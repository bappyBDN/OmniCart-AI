import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ProductPage from './ProductPage'
import { ProductCards } from './Chat'
import { productIdFromPath, productPath } from './productLinks'
import { api } from './api'

vi.mock('./api', async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const product = { product_id: 'SKU_183', name: 'Batteries Pack', category: 'Electronics',
  price: 18.99, effective_price: 14.99, stock_count: 80, colors: ['Alkaline'], sizes: ['AA 24-pack'],
  description: 'Long lasting, leak proof.', image_url: '/api/products/SKU_183/image', image_kind: 'representative' }

describe('SKU-specific product pages', () => {
  it('creates and reads a stable product URL', () => {
    expect(productPath('SKU_183')).toBe('/products/SKU_183')
    expect(productIdFromPath('/products/SKU_183')).toBe('SKU_183')
    expect(productIdFromPath('/products/SKU_183/')).toBe('SKU_183')
    expect(productIdFromPath('/')).toBeNull()
    expect(productIdFromPath('/products/%')).toBe('__invalid_product_id__')
  })

  it('loads exactly one product for an unauthenticated Messenger visitor', async () => {
    api.mockResolvedValue(product)
    const review = vi.fn()
    render(<ProductPage productId="SKU_183" token="" onReview={review} />)
    await screen.findByRole('heading', { name: 'Batteries Pack' })
    expect(api.mock.calls[0][0]).toBe('/public/products/SKU_183')
    expect(screen.getAllByText('$14.99').length).toBeGreaterThan(0)
    expect(screen.getAllByText('80 in stock').length).toBeGreaterThan(0)
    expect(screen.getByText('Representative image — may not show the exact SKU')).toBeInTheDocument()
    expect(review).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Full name')).toBeInTheDocument()
    expect(screen.getByLabelText('Phone number')).toHaveAttribute('type', 'tel')
  })

  it('does not silently show another product for a missing SKU', async () => {
    api.mockRejectedValue(Object.assign(new Error('Not found'), { status: 404 }))
    render(<ProductPage productId="SKU_MISSING" token="" />)
    await screen.findByRole('heading', { name: 'Product unavailable' })
    expect(screen.queryByText('Batteries Pack')).not.toBeInTheDocument()
  })

  it('handles a broken saved image without substituting another product photo', async () => {
    api.mockResolvedValue(product)
    render(<ProductPage productId="SKU_183" token="" />)
    await screen.findByRole('heading', { name: 'Batteries Pack' })
    fireEvent.error(screen.getByRole('img'))
    expect(screen.getByText('Photo unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('requires an explicit review action even with an existing session', async () => {
    api.mockResolvedValue(product)
    const review = vi.fn().mockResolvedValue(undefined)
    const profile = { name: 'Test Shopper', phone: '01712345678', address: 'House 12, Road 4, Dhaka' }
    render(<ProductPage productId="SKU_183" token="private-session" profile={profile} onReview={review} />)
    await screen.findByRole('heading', { name: 'Batteries Pack' })
    expect(review).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Product quantity'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review checkout' }))
    await waitFor(() => expect(review).toHaveBeenCalledWith(product, 2, profile))
  })

  it('links a web chat card to the exact SKU, not the whole catalog', () => {
    render(<ProductCards sources={[{ id: 'product:SKU_183', kind: 'product', title: product.name, data: product }]} />)
    expect(screen.getByRole('link', { name: 'View product' })).toHaveAttribute('href', '/products/SKU_183')
  })
})
