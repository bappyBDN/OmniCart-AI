import { useEffect, useState } from 'react'
import { ArrowLeft, LoaderCircle } from 'lucide-react'
import { api, currency } from './api'
import './ProductPage.css'
import ProductOrderForm from './ProductOrderForm'

export default function ProductPage({ productId, token, profile, onReview }) {
  const [product, setProduct] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const [imageFailed, setImageFailed] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setProduct(null); setError(''); setImageFailed(false)
    api(`/public/products/${encodeURIComponent(productId)}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setProduct(value) })
      .catch(failure => {
        if (!controller.signal.aborted) setError(failure.status === 404 ? 'This product is not available.' : 'Could not load the product. Please try again.')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [productId, attempt])

  if (loading) return <p className="loading"><LoaderCircle size={18} className="spin" />Loading product…</p>
  if (error) return <section className="empty-state"><h1>Product unavailable</h1><p>{error}</p><button className="secondary" onClick={() => setAttempt(n => n + 1)}>Try again</button><a href="/">Back to storefront</a></section>
  const imageNote = product.image_kind === 'photo' ? 'Product photo' : product.image_kind === 'representative'
    ? 'Representative image — may not show the exact SKU' : 'Illustration — product photo not supplied'
  return <section className="product-detail" aria-label="Product details">
    <a className="product-detail-back" href="/"><ArrowLeft size={16} />Back to storefront</a>
    <div className="product-detail-layout">
      <figure>{imageFailed ? <div className="product-detail-no-photo">Photo unavailable</div>
        : <img src={product.image_url} alt={product.image_alt || product.name} onError={() => setImageFailed(true)} />}
        <figcaption>{imageFailed ? 'The saved image could not be loaded.' : imageNote}</figcaption></figure>
      <div className="product-detail-copy">
        <span className="eyebrow">{product.category} / {product.product_id}</span>
        <h1>{product.name}</h1><p>{product.description}</p>
        <div className="product-detail-price"><strong>{currency(product.effective_price)}</strong>{product.effective_price < product.price && <del>{currency(product.price)}</del>}</div>
        <p>{product.stock_count > 0 ? `${product.stock_count} in stock` : 'Currently out of stock'}</p>
        <dl><dt>Colors / variants</dt><dd>{product.colors?.join(' · ') || 'Not listed'}</dd><dt>Sizes</dt><dd>{product.sizes?.join(' / ') || 'Not listed'}</dd></dl>
        <a className="primary product-detail-order-link" href="#order-form">Edit delivery details &amp; order</a>
        <p className="small muted">Prices are merchandise-only in USD. Opening this link does not create or confirm an order.</p>
      </div>
    </div>
    <ProductOrderForm key={product.product_id} product={product} token={token} profile={profile} onReview={onReview} />
  </section>
}
