import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, Headphones, Home, Package, Search, Shirt, SlidersHorizontal, Watch } from 'lucide-react'
import { currency } from './api'
import { productPath } from './productLinks'

export default function Catalog({ request, notify, onPrepare, revision }) {
  const [products, setProducts] = useState([])
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('All')
  const [loading, setLoading] = useState(true)
  const [quantities, setQuantities] = useState({})
  const [pending, setPending] = useState('')
  useEffect(() => {
    let active = true
    setLoading(true)
    const timer = setTimeout(() => request(`/products${query ? `?q=${encodeURIComponent(query)}` : ''}`).then(value => active && setProducts(value)).catch(error => notify(error.message, true)).finally(() => active && setLoading(false)), query ? 300 : 0)
    return () => { active = false; clearTimeout(timer) }
  }, [query, request, notify, revision])
  const categories = useMemo(() => ['All', ...new Set(products.map(p => p.category).filter(Boolean))], [products])
  const visible = products.filter(p => category === 'All' || p.category === category)
  return <><div className="section-header"><div><span className="eyebrow">CURATED FOR EVERYDAY LIFE</span><h1>Find your next favorite.</h1><p>Browse the real catalog. Live prices, honest availability.</p></div><span className="count-badge">{products.length} products</span></div><div className="catalog-toolbar"><div className="search-field"><Search size={18} /><input aria-label="Search products" placeholder="Search in English or বাংলা…" value={query} onChange={event => { setQuery(event.target.value); setCategory('All') }} /><kbd>⌕</kbd></div><SlidersHorizontal size={18} className="muted" /><select aria-label="Filter category" value={category} onChange={event => setCategory(event.target.value)}>{categories.map(c => <option key={c}>{c}</option>)}</select></div><div className="product-grid">{loading ? <p className="loading">Finding good things…</p> : !visible.length ? <div className="empty-state"><Search size={30} /><h3>No matching products</h3><p>Try a product name, category, or a different description.</p></div> : visible.map((p, index) => {
    const Icon = p.category === 'Apparel' ? Shirt : p.category === 'Electronics' ? Headphones : p.category === 'Home' ? Home : p.category === 'Accessories' ? Watch : Package
    const stock = p.stock_count
    const stockLabel = stock === null || stock === undefined ? 'Stock needs confirmation' : stock > 0 ? `${stock} in stock` : 'Sold out'
    const inStock = stock > 0
    const orderable = p.orderable === undefined ? (p.effective_price != null && inStock) : p.orderable === true
    const colors = p.colors?.length ? p.colors.join(' · ') : 'Colors not listed'
    const sizes = p.sizes?.length ? p.sizes.join(' / ') : 'Sizes not listed'
    const discount = p.effective_price != null && p.price != null && p.effective_price < p.price ? Math.round((1 - p.effective_price / p.price) * 100) : 0
    return <article className="product-card" key={p.product_id}>
      <a className={`product-visual color-${index % 5}`} href={productPath(p.product_id)} aria-label={`View ${p.name}`}>
        <Icon className="product-visual-icon" size={65} strokeWidth={1.1} />
        {p.image_url && <img className="product-image" src={p.image_url} alt={`${p.name} product`} loading="lazy" onError={event => { event.currentTarget.style.display = 'none' }} />}
        <span className={`availability ${inStock ? '' : 'unavailable'}`}><i />{stockLabel}</span>
        {discount > 0 && <span className="discount-label">-{discount}%</span>}
      </a>
      <div className="product-info">
        <a className="product-title-link" href={productPath(p.product_id)}>
          <span className="eyebrow">{p.category} <span className="muted">/ {p.product_id}</span></span>
          <h3>{p.name}</h3>
        </a>
        <p>{p.description}</p>
        <div className="product-attributes"><span>{colors}</span><span>{sizes}</span></div>
        <div className="product-price"><strong>{currency(p.effective_price)}</strong>{discount > 0 && <del>{currency(p.price)}</del>}<small>{stockLabel}</small></div>
        <div className="product-actions">
          <input aria-label={`Quantity for ${p.name}`} type="number" min="1" max={Math.min(1000, Math.max(1, stock || 1))} value={quantities[p.product_id] ?? 1} disabled={!orderable} onChange={event => setQuantities({ ...quantities, [p.product_id]: event.target.value })} />
          <button className="secondary" disabled={!orderable || !!pending} onClick={async () => {
            const quantity = Number(quantities[p.product_id] ?? 1)
            if (!Number.isInteger(quantity) || quantity < 1 || quantity > Math.min(1000, stock)) { notify('Enter a valid quantity within available stock.', true); return }
            setPending(p.product_id); try { await onPrepare(p, quantity) } finally { setPending('') }
          }}>{pending === p.product_id ? 'Preparing…' : 'Review checkout'}<ArrowUpRight size={15} /></button>
        </div>
      </div>
    </article>
  })}</div><p className="small muted">Click a product to see its details. Prices are merchandise-only in taka (৳); delivery and payment fees are not included.</p></>
}
