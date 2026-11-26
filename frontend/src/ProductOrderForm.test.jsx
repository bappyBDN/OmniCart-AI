import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ProductOrderForm from './ProductOrderForm'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const product = { product_id: 'SKU_183', effective_price: 14.99, stock_count: 80 }
const details = { name: 'Mobile Shopper', phone: '01712345678', address: 'House 12, Road 4, Dhaka 1205' }

function fill() {
  fireEvent.change(screen.getByLabelText('Full name'), { target: { value: details.name } })
  fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: details.phone } })
  fireEvent.change(screen.getByLabelText('Full delivery address'), { target: { value: details.address } })
}

describe('mobile product order form', () => {
  it('collects guest details and quantity before requesting a review', async () => {
    const review = vi.fn().mockResolvedValue({})
    render(<ProductOrderForm product={product} token="" onReview={review} />)
    expect(review).not.toHaveBeenCalled()
    fill()
    fireEvent.click(screen.getByRole('button', { name: 'Increase quantity' }))
    expect(screen.getByText('৳29.98')).toBeInTheDocument()
    fireEvent.submit(screen.getByRole('button', { name: 'Review checkout' }).closest('form'))
    await waitFor(() => expect(review).toHaveBeenCalledWith(product, 2, details))
  })

  it('prevents duplicate reviews while a request is pending', async () => {
    let release
    const review = vi.fn(() => new Promise(resolve => { release = resolve }))
    render(<ProductOrderForm product={product} token="" onReview={review} />)
    fill()
    const form = screen.getByRole('button', { name: 'Review checkout' }).closest('form')
    fireEvent.submit(form); fireEvent.submit(form)
    expect(review).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Phone number')).toBeDisabled()
    await act(async () => release({}))
  })

  it('does not prepare an order with an invalid phone number', () => {
    const review = vi.fn()
    render(<ProductOrderForm product={product} token="" onReview={review} />)
    fill()
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '123' } })
    fireEvent.submit(screen.getByRole('button', { name: 'Review checkout' }).closest('form'))
    expect(screen.getByRole('alert')).toHaveTextContent('7–15 digits')
    expect(review).not.toHaveBeenCalled()
  })

  it('shows server failures without clearing the edited delivery form', async () => {
    render(<ProductOrderForm product={product} token="" onReview={vi.fn().mockRejectedValue(new Error('Only 1 unit remains.'))} />)
    fill()
    fireEvent.submit(screen.getByRole('button', { name: 'Review checkout' }).closest('form'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Only 1 unit remains.')
    expect(screen.getByLabelText('Phone number')).toHaveValue(details.phone)
    expect(screen.getByLabelText('Full delivery address')).toHaveValue(details.address)
  })

  it('prefills saved details without overwriting edits, and clears them on logout', async () => {
    const { rerender } = render(<ProductOrderForm product={product} token="session" profile={details} />)
    expect(screen.getByLabelText('Phone number')).toHaveValue(details.phone)
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '01898765432' } })
    rerender(<ProductOrderForm product={product} token="session" profile={{ ...details, name: 'Updated Name' }} />)
    expect(screen.getByLabelText('Full name')).toHaveValue('Updated Name')
    expect(screen.getByLabelText('Phone number')).toHaveValue('01898765432')
    rerender(<ProductOrderForm product={product} token="" profile={null} />)
    await waitFor(() => expect(screen.getByLabelText('Phone number')).toHaveValue(''))
  })

  it('shares a SKU link without names, phones or session tokens', () => {
    render(<ProductOrderForm product={product} token="private-token" profile={details} />)
    const link = screen.getByLabelText("Share this product's order form").value
    const parsed = new URL(link)
    expect(parsed.pathname).toBe('/products/SKU_183')
    expect(parsed.hash).toBe('#order-form')
    expect(parsed.search).toBe('')
    expect(link).not.toContain(details.phone)
    expect(link).not.toContain('private-token')
  })

  it('disables order review for sold-out products', () => {
    render(<ProductOrderForm product={{ ...product, stock_count: 0 }} token="" />)
    expect(screen.getByRole('button', { name: 'Review checkout' })).toBeDisabled()
  })
})
