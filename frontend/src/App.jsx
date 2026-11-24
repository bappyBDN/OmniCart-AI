import { useCallback, useEffect, useState } from 'react'
import { ArrowUpRight, BookOpen, Check, ChevronRight, CircleHelp, LayoutGrid, LogOut, MessageSquare, Package, PanelLeftClose, Settings2, ShieldCheck, ShoppingBag, Sparkles, UserRound, X } from 'lucide-react'
import { api, currency } from './api'
import Chat from './Chat'
import Catalog from './Catalog'
import Admin from './Admin'

const navigation = [
  ['chat', MessageSquare, 'AI assistant'], ['catalog', LayoutGrid, 'Explore products'],
  ['orders', Package, 'My orders'], ['policies', BookOpen, 'Store policies'], ['profile', UserRound, 'My profile'],
]

export default function App() {
  const [page, setPage] = useState('chat')
  const [token, setToken] = useState(() => localStorage.getItem('omnicart-session') || '')
  const [health, setHealth] = useState(null)
  const [profile, setProfile] = useState(null)
  const [notice, setNotice] = useState(null)
  const [mobileNav, setMobileNav] = useState(false)
  const [starting, setStarting] = useState(false)
  const [checkout, setCheckout] = useState(null)
  const [contact, setContact] = useState({ phone: '', address: '' })
  const [confirming, setConfirming] = useState(false)
  const [revision, setRevision] = useState(0)
  const notify = useCallback((message, error = false) => setNotice({ message, error }), [])
  const request = useCallback((path, options = {}) => api(path, { token, ...options }), [token])

  useEffect(() => {
    let active = true
    const refresh = () => api('/health').then(value => active && setHealth(value)).catch(() => active && setHealth(null))
    refresh()
    const interval = setInterval(refresh, 15000)
    return () => { active = false; clearInterval(interval) }
  }, [])

  useEffect(() => {
    if (!token) { setProfile(null); return }
    let active = true
    request('/profile').then(value => active && setProfile(value)).catch(error => {
      if (!active) return
      if (error.status === 401) { localStorage.removeItem('omnicart-session'); setToken('') }
      else notify(error.message, true)
    })
    return () => { active = false }
  }, [token, revision, request, notify])

  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(null), 7000)
    return () => clearTimeout(timer)
  }, [notice])

  async function start() {
    setStarting(true)
    try {
      const session = await api('/sessions', { method: 'POST' })
      localStorage.setItem('omnicart-session', session.token)
      setToken(session.token); setProfile(session.profile)
    } catch (error) { notify(error.message, true) }
    finally { setStarting(false) }
  }

  async function logout() {
    try { await request('/sessions/current', { method: 'DELETE' }) } catch { /* Local logout still succeeds. */ }
    localStorage.removeItem('omnicart-session'); setToken(''); setProfile(null); setCheckout(null); setContact({ phone: '', address: '' })
  }

  function openCheckout(draft) {
    setContact({
      phone: draft.phone || profile?.phone || '',
      address: draft.address || profile?.address || '',
    })
    setCheckout(draft)
  }

  async function prepare(product, quantity) {
    try {
      const draft = await request('/checkout', { method: 'POST', body: { product_id: product.product_id, quantity } })
      if (draft.error === 'profile_incomplete') { setPage('profile'); notify(`Please complete: ${draft.missing.join(', ')}.`, true); return }
      if (draft.error) { notify(`Only ${draft.available ?? 0} items are available.`, true); return }
      openCheckout(draft)
    } catch (error) { notify(error.message, true) }
  }

  async function confirm() {
    const phone = contact.phone.trim()
    const address = contact.address.trim()
    if (!/^[0-9+()\-\s]{7,30}$/.test(phone) || phone.replace(/\D/g, '').length < 7) {
      notify('Enter a valid phone number (7–15 digits).', true); return
    }
    if (address.length < 12) { notify('Enter the complete delivery address (house, road, area, city).', true); return }
    setConfirming(true)
    try {
      const receipt = await request(`/checkout/${checkout.draft_id}/confirm`, {
        method: 'POST', body: { phone, address },
      })
      notify(`${receipt.reference} received. Email: ${receipt.email_status.replaceAll('_', ' ')}.`)
      setCheckout(null); setPage('orders'); setRevision(value => value + 1)
    } catch (error) { notify(error.message, true) }
    finally { setConfirming(false) }
  }

  const navigate = next => {
    if (next === 'admin') { window.location.assign(import.meta.env.DEV ? '/admin.html' : '/admin'); return }
    setPage(next); setMobileNav(false)
  }
  return <div className="app-shell">
    <aside className={`sidebar ${mobileNav ? 'open' : ''}`}>
      <a href="#" className="brand" onClick={event => { event.preventDefault(); navigate('chat') }}><span className="brand-icon"><ShoppingBag size={23} /></span><span>omnicart<span className="brand-dot">.</span><small>THE SHOPPING COPILOT</small></span></a>
      <div className="workspace-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{navigation.map(([key, Icon, label]) => <button key={key} className={`nav-item ${page === key ? 'active' : ''}`} onClick={() => navigate(key)}><Icon size={19} />{label}{page === key && <span className="nav-indicator" />}</button>)}</nav>
      <div className="sidebar-note"><span className="tiny-icon"><Sparkles size={16} /></span><strong>A little smarter.<br />A lot more helpful.</strong><p>Real products. Verified policies.<br />Answers you can trace.</p><span className="note-pill">Powered by LangGraph <ArrowUpRight size={12} /></span></div>
      <div className="sidebar-bottom"><button className={`nav-item ${page === 'admin' ? 'active' : ''}`} onClick={() => navigate('admin')}><Settings2 size={18} />Shop administration</button><div className="account"><span className="avatar">{profile?.name?.slice(0, 1).toUpperCase() || 'G'}</span><div><strong>{profile?.name || 'Guest shopper'}</strong><small>{token ? 'Private shopping session' : 'Welcome to OmniCart'}</small></div>{token && <button className="icon-button" aria-label="Log out" onClick={logout}><LogOut size={16} /></button>}</div></div>
    </aside>
    {mobileNav && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
    <main className="main-content">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-toggle" aria-label="Toggle navigation" onClick={() => setMobileNav(!mobileNav)}><PanelLeftClose size={21} /></button><span>Workspace</span><ChevronRight size={13} /><strong>{navigation.find(item => item[0] === page)?.[2] || 'Administration'}</strong></div><div className="topbar-right"><span className={`connection ${health ? '' : 'offline'}`}><i />{health ? 'System online' : 'Connecting…'}</span><span className="topbar-divider" /><span className="store-name">{health?.shop_name || 'OmniCart'}</span><ShieldCheck size={18} className="muted" /></div></header>
      <div className="page-content">
        {page === 'admin' ? <Admin notify={notify} /> : !token ? <section className="welcome-card"><span className="eyebrow"><Sparkles size={14} /> YOUR PERSONAL SHOPPING COPILOT</span><h1>Good finds start with<br />a great conversation<span>.</span></h1><p>Explore the store, get answers grounded in real information,<br />and check out with confidence. English or বাংলা — your choice.</p><button className="primary" disabled={starting || !health?.shop_ready} onClick={start}>{starting ? 'Starting your session…' : 'Start shopping'} <ArrowUpRight size={17} /></button><div className="welcome-benefits"><span><ShieldCheck size={16} /> Private guest session</span><span><BookOpen size={16} /> Source-backed answers</span></div><p className="small muted">Your browser stores a private access token. No password or email is needed to begin.</p></section> : <>
          {page === 'chat' && <Chat key={token} request={request} health={health} profile={profile} setProfile={setProfile} notify={notify} onCheckout={openCheckout} onNavigate={navigate} />}
          {page === 'catalog' && <Catalog request={request} notify={notify} onPrepare={prepare} revision={revision} />}
          {page === 'orders' && <Orders request={request} notify={notify} revision={revision} />}
          {page === 'policies' && <Policies request={request} notify={notify} />}
          {page === 'profile' && <Profile profile={profile} request={request} notify={notify} onSave={value => { setProfile(value); setRevision(n => n + 1) }} />}
        </>}
      </div>
      <footer className="site-footer"><span><ShieldCheck size={13} /> Built for grounded conversations</span><span>LangChain + LangGraph <span className="footer-dot">·</span> OmniCart v2.0</span></footer>
    </main>
    {notice && <div className={`toast ${notice.error ? 'error' : ''}`} role="status">{notice.error ? <CircleHelp size={18} /> : <Check size={18} />}<span>{notice.message}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice(null)}><X size={16} /></button></div>}
    {checkout && <CheckoutModal checkout={checkout} contact={contact} setContact={setContact}
      confirming={confirming} onClose={() => setCheckout(null)} onConfirm={confirm} />}
  </div>
}

function CheckoutModal({ checkout, contact, setContact, confirming, onClose, onConfirm }) {
  const phoneDigits = contact.phone.replace(/\D/g, '')
  const ready = phoneDigits.length >= 7 && phoneDigits.length <= 15 && contact.address.trim().length >= 12
  return <div className="modal-backdrop">
    <section className="modal" role="dialog" aria-modal="true" aria-labelledby="checkout-heading">
      <button className="modal-close icon-button" aria-label="Close checkout" disabled={confirming} onClick={onClose}><X /></button>
      <span className="large-icon"><ShoppingBag /></span>
      <h2 id="checkout-heading">One last look.</h2>
      <p className="muted">Nothing is ordered until you confirm.</p>
      <div className="receipt-row"><strong>{checkout.product_name}</strong><span>× {checkout.quantity}</span></div>
      <div className="receipt-row"><span>Unit price</span><span>{currency(checkout.unit_price)}</span></div>
      <div className="receipt-row total"><strong>Merchandise subtotal</strong><strong>{currency(checkout.total_price)}</strong></div>
      <p className="small muted">{checkout.note}</p>
      <form className="checkout-form" onSubmit={event => { event.preventDefault(); if (ready) onConfirm() }}>
        <fieldset className="checkout-contact" disabled={confirming}>
          <legend>Delivery details</legend>
          <label>Phone number
            <input name="phone" type="tel" autoComplete="tel" inputMode="tel" required
              minLength={7} maxLength={30} placeholder="e.g. 01712 345678"
              value={contact.phone} onChange={event => setContact({ ...contact, phone: event.target.value })} />
          </label>
          <label>Full delivery address
            <textarea name="address" autoComplete="street-address" required minLength={12} maxLength={1000}
              rows={3} placeholder="House, road, area, city, postal code"
              value={contact.address} onChange={event => setContact({ ...contact, address: event.target.value })} />
          </label>
          <p className="small muted">Used for this delivery and saved to your profile.</p>
        </fieldset>
        <button type="submit" className="primary full-width" disabled={confirming || !ready}>
          {confirming ? 'Confirming…' : 'Confirm order'}<Check size={17} />
        </button>
        {!ready && <p className="checkout-hint">Enter a phone number and full delivery address to continue.</p>}
      </form>
      <p className="small muted center">No online payment will be collected.</p>
    </section>
  </div>
}

function SectionHeader({ eyebrow, title, description, children }) {
  return <div className="section-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{children}</div>
}

function Orders({ request, notify, revision }) {
  const [orders, setOrders] = useState(null)
  useEffect(() => {
    let active = true
    const refresh = () => request('/orders').then(value => active && setOrders(value)).catch(error => active && notify(error.message, true))
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [request, notify, revision])
  return <><SectionHeader eyebrow="YOUR PURCHASES" title="Every order, in one place." description="Real-time order status, straight from the store." />{orders === null ? <p className="loading">Loading your orders…</p> : !orders.length ? <div className="empty-state"><Package size={36} /><h3>No orders just yet</h3><p>Your confirmed orders will appear here.</p></div> : <div className="order-list">{orders.map(order => <article className="order-card" key={order.order_id}><div><span className="eyebrow">{order.reference}</span><h3>{order.product_name}</h3><p>{order.quantity} item(s) · {new Date(order.created_at).toLocaleDateString()}</p><small>Email: {order.email_status.replaceAll('_', ' ')}</small></div><div className="order-right"><span className={`status status-${order.status.toLowerCase()}`}>{order.status}</span><strong>{currency(order.total_price)}</strong><small>Merchandise subtotal</small></div></article>)}</div>}</>
}

function Policies({ request, notify }) {
  const [policies, setPolicies] = useState([])
  const [filter, setFilter] = useState('')
  useEffect(() => { request('/policies').then(setPolicies).catch(error => notify(error.message, true)) }, [request, notify])
  return <><SectionHeader eyebrow="THE FINE PRINT, MADE SIMPLE" title="Clear policies. No surprises." description="The original store rules used by your shopping assistant." /><input className="search-input" aria-label="Filter policies" placeholder="Search policies…" value={filter} onChange={event => setFilter(event.target.value)} /><div className="policy-list">{policies.filter(p => `${p.policy_type} ${p.policy_text}`.toLowerCase().includes(filter.toLowerCase())).map(policy => <details className="policy-card" key={policy.policy_id}><summary><BookOpen size={17} /><strong>{policy.policy_type}</strong><ChevronRight size={16} /></summary><p>{policy.policy_text}</p><small className="muted">Source: policy:{policy.policy_id}</small></details>)}</div></>
}

function Profile({ profile, request, notify, onSave }) {
  const [form, setForm] = useState({ name: '', phone: '', email: '', address: '' })
  const [saving, setSaving] = useState(false)
  useEffect(() => { if (profile) setForm(Object.fromEntries(['name', 'phone', 'email', 'address'].map(key => [key, profile[key] || '']))) }, [profile])
  async function save(event) {
    event.preventDefault(); setSaving(true)
    try { onSave(await request('/profile', { method: 'PATCH', body: form })); notify('Your profile is saved. You’re ready for checkout.') }
    catch (error) { notify(error.message, true) }
    finally { setSaving(false) }
  }
  return <>
    <SectionHeader eyebrow="MAKE YOURSELF AT HOME" title="Your shopping profile." description="Only the information needed to fulfill your order. Email is optional." />
    <form className="profile-form panel" onSubmit={save}>
      <div className="form-grid">{[['name', 'Full name', 'text'], ['phone', 'Phone number', 'tel'], ['email', 'Email address', 'email']].map(([key, label, type]) =>
        <label key={key}>{label}<input type={type} value={form[key]} autoComplete={key === 'name' ? 'name' : type === 'tel' ? 'tel' : 'email'} maxLength={key === 'name' ? 120 : key === 'phone' ? 30 : 254} required={key !== 'email'} onChange={event => setForm({ ...form, [key]: event.target.value })} /></label>
      )}</div>
      <label>Delivery address<textarea required value={form.address} autoComplete="street-address" maxLength={1000} onChange={event => setForm({ ...form, address: event.target.value })} rows={3} /></label>
      <div className="form-footer"><span><ShieldCheck size={16} />Saved only to your private session</span><button className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save profile'}<Check size={16} /></button></div>
    </form>
    <p className="small muted">This is a guest session, not an email-authenticated account. Keep this browser’s private session to return to your orders.</p>
  </>
}
