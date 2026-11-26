import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, Copy, LoaderCircle, Minus, Plus, ShieldCheck } from 'lucide-react'
import { currency } from './api'
import { deliveryError } from './checkoutValidation'
import { productPath } from './productLinks'

const blankDetails = () => ({ name: '', phone: '', address: '' })

export default function ProductOrderForm({ product, token, profile, onReview }) {
  const [details, setDetails] = useState(blankDetails)
  const [quantity, setQuantity] = useState(1)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [copyMessage, setCopyMessage] = useState('')
  const dirty = useRef(new Set())
  const previousToken = useRef(token)
  const submitting = useRef(false)
  const formRef = useRef(null)
  const linkRef = useRef(null)
  const orderable = product.orderable === undefined
    ? product.effective_price != null && (product.stock_count || 0) > 0
    : product.orderable === true
  const max = Math.min(1000, Math.max(1, product.stock_count || 1))
  const qty = Number(quantity)
  const validQuantity = Number.isInteger(qty) && qty >= 1 && qty <= max
  const orderLink = `${window.location.origin}${productPath(product.product_id)}#order-form`

  useEffect(() => {
    setDetails(previous => Object.fromEntries(Object.keys(previous).map(key =>
      [key, dirty.current.has(key) ? previous[key] : profile?.[key] || ''])))
  }, [profile])
  useEffect(() => {
    if (previousToken.current && !token) {
      dirty.current.clear(); setDetails(blankDetails()); setError('')
    }
    previousToken.current = token
  }, [token])
  useEffect(() => {
    if (window.location.hash === '#order-form') formRef.current?.scrollIntoView?.({ block: 'start' })
  }, [product.product_id])

  function edit(key, value) {
    dirty.current.add(key)
    setDetails(previous => ({ ...previous, [key]: value }))
    setError('')
  }
  async function review(event) {
    event.preventDefault()
    if (submitting.current) return
    const validation = deliveryError(details)
    if (validation) { setError(validation); return }
    if (!orderable) { setError('Price, currency and stock need merchant confirmation before checkout.'); return }
    if (!product.stock_count || !validQuantity) { setError('Choose a quantity within the available stock.'); return }
    submitting.current = true; setPending(true); setError('')
    try {
      await onReview(product, qty, Object.fromEntries(Object.entries(details).map(([key, value]) => [key, value.trim()])))
    } catch (failure) {
      setError(failure.message || 'Could not prepare checkout. Your order has not been confirmed.')
    } finally { submitting.current = false; setPending(false) }
  }
  async function copyLink() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(orderLink)
      setCopyMessage('Order link copied')
    } catch {
      linkRef.current?.focus(); linkRef.current?.select()
      setCopyMessage('Select and copy the order link below')
    }
  }

  return <>
    <section className="product-order-panel" id="order-form" ref={formRef} aria-labelledby="product-order-heading">
      <div className="product-order-heading"><span className="eyebrow">YOUR ORDER, YOUR DETAILS</span><h2 id="product-order-heading">Delivery details</h2>
        <p>Edit your name, phone and address below. You'll review the order before confirming.</p></div>
      <form className="product-order-form" onSubmit={review}>
        <fieldset disabled={pending}><legend className="visually-hidden">Product order details</legend>
          <div className="product-order-quantity"><label htmlFor="product-quantity">Quantity</label><div className="quantity-stepper">
            <button type="button" aria-label="Decrease quantity" disabled={qty <= 1 || !orderable} onClick={() => setQuantity(Math.max(1, (Number(quantity) || 1) - 1))}><Minus size={18} /></button>
            <input id="product-quantity" aria-label="Product quantity" type="number" inputMode="numeric" min="1" max={max} required value={quantity} disabled={!orderable} onChange={event => { setQuantity(event.target.value); setError('') }} />
            <button type="button" aria-label="Increase quantity" disabled={qty >= max || !orderable} onClick={() => setQuantity(Math.min(max, (Number(quantity) || 1) + 1))}><Plus size={18} /></button>
          </div><strong>{orderable ? currency(product.effective_price * (validQuantity ? qty : 0)) : 'Price needs confirmation'}</strong><small>Estimated merchandise subtotal</small></div>
          <div className="product-order-contact">
            <label>Full name<input name="name" autoComplete="name" maxLength={120} required value={details.name} onChange={event => edit('name', event.target.value)} placeholder="Your full name" /></label>
            <label>Phone number<input name="phone" type="tel" inputMode="tel" autoComplete="tel" minLength={7} maxLength={30} required value={details.phone} onChange={event => edit('phone', event.target.value)} placeholder="e.g. +880 1712 345678" /></label>
            <label className="product-order-address">Full delivery address<textarea name="address" autoComplete="street-address" minLength={12} maxLength={1000} required rows={3} value={details.address} onChange={event => edit('address', event.target.value)} placeholder="House, road, area, city and postal code" /></label>
          </div>
        </fieldset>
        {error && <p className="product-order-error" role="alert">{error}</p>}
        <button className="primary product-review-button" type="submit" disabled={pending || !orderable || !validQuantity}>
          {pending ? <><LoaderCircle size={18} className="spin" />Preparing your review…</> : <>Review checkout <ArrowRight size={18} /></>}
        </button>
        <p className="product-order-privacy"><ShieldCheck size={15} />{token ? 'Details are saved to your private shopping session.' : 'A private shopping session is created only when you request checkout.'}</p>
        <p className="small muted">Nothing is ordered or deducted from stock until you confirm. No online payment is collected here. Delivery/payment fees are not included. For changes after ordering, contact the shop.</p>
      </form>
      <div className="product-order-share"><label htmlFor="product-order-link">Share this product's order form</label><div><input id="product-order-link" ref={linkRef} readOnly value={orderLink} /><button className="secondary" type="button" onClick={copyLink}>{copyMessage === 'Order link copied' ? <Check size={17} /> : <Copy size={17} />}Copy order link</button></div><span role="status">{copyMessage}</span></div>
    </section>
    <div className="product-mobile-order-bar"><div><strong>{orderable ? currency(product.effective_price) : 'Price needs confirmation'}</strong><small>{product.stock_count !== null && product.stock_count !== undefined ? `${product.stock_count} in stock` : 'Stock needs confirmation'}</small></div>
      {orderable && product.stock_count ? <a className="primary" href="#order-form">Order this product <ArrowRight size={17} /></a> : <span>Currently unavailable</span>}
    </div>
  </>
}
