import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, Headphones, Home, Package, Search, Shirt, SlidersHorizontal, Watch } from 'lucide-react'
import { currency } from './api'

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
    return <article className="product-card" key={p.product_id}><div className={`product-visual color-${index % 5}`}><Icon className="product-visual-icon" size={65} strokeWidth={1.1} />{p.image_url && <img className="product-image" src={p.image_url} alt={`${p.name} product`} loading="lazy" onError={event => { event.currentTarget.style.display = 'none' }} />}<span className={`availability ${p.stock_count ? '' : 'unavailable'}`}><i />{p.stock_count ? 'In stock' : 'Sold out'}</span>{p.effective_price < p.price && <span className="discount-label">-{Math.round((1 - p.effective_price / p.price) * 100)}%</span>}</div><div className="product-info"><span className="eyebrow">{p.category} <span className="muted">/ {p.product_id}</span></span><h3>{p.name}</h3><p>{p.description}</p><div className="product-attributes"><span>{p.colors.join(' · ')}</span><span>{p.sizes.join(' / ')}</span></div><div className="product-price"><strong>{currency(p.effective_price)}</strong>{p.effective_price < p.price && <del>{currency(p.price)}</del>}<small>{p.stock_count} left</small></div><div className="product-actions"><input aria-label={`Quantity for ${p.name}`} type="number" min="1" max={Math.min(1000, Math.max(1, p.stock_count))} value={quantities[p.product_id] ?? 1} disabled={!p.stock_count} onChange={event => setQuantities({ ...quantities, [p.product_id]: event.target.value })} /><button className="secondary" disabled={!p.stock_count || !!pending} onClick={async () => {
      const quantity = Number(quantities[p.product_id] ?? 1)
      if (!Number.isInteger(quantity) || quantity < 1 || quantity > Math.min(1000, p.stock_count)) { notify('Enter a valid quantity within available stock.', true); return }
      setPending(p.product_id); try { await onPrepare(p, quantity) } finally { setPending('') }
    }}>{pending === p.product_id ? 'Preparing…' : 'Review checkout'}<ArrowUpRight size={15} /></button></div></div></article>
  })}</div><p className="small muted">Product images are representative catalogue images for this local build; replace them with licensed merchant photography in Admin → Catalogue → Image. Prices are merchandise-only in USD.</p></>
}
