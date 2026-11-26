import { useCallback, useEffect, useState } from 'react'
import {
  Activity, ArrowRight, BarChart3, BookOpen, Check, ChevronRight, CircleDollarSign,
  Clock3, LayoutDashboard, LogOut, MessageSquare, Package, Plus, RefreshCw, Save,
  Search, ShieldCheck, ShoppingBag, TrendingUp, Users, X,
} from 'lucide-react'
import { api, currency } from './api'
import { AuditTrail, KnowledgeStudio, SupportDesk } from './OperationsWorkspace'

const formatCost = value => {
  const amount = Number(value || 0)
  return `$${amount.toFixed(amount > 0 && amount < 0.01 ? 5 : 2)}`
}

const navigation = [
  ['overview', LayoutDashboard, 'Overview'],
  ['orders', ShoppingBag, 'Orders'],
  ['products', Package, 'Catalogue'],
  ['customers', Users, 'Customers'],
  ['support', MessageSquare, 'Support desk'],
  ['knowledge', BookOpen, 'Knowledge studio'],
  ['policies', ShieldCheck, 'Policies'],
  ['audit', Activity, 'Audit trail'],
]

export default function Admin({ notify }) {
  const [token, setToken] = useState(() => sessionStorage.getItem('omnicart-admin') || '')
  const [credentials, setCredentials] = useState({ email: '', password: '' })
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [health, setHealth] = useState(null)
  const [tab, setTab] = useState('overview')
  const [editing, setEditing] = useState(null)
  const [selected, setSelected] = useState(null)
  const [messages, setMessages] = useState([])
  const [reply, setReply] = useState('')
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('All')
  const request = useCallback((path, options = {}) => api(`/admin${path}`, { token, ...options }), [token])

  const refresh = useCallback(async () => {
    try {
      const [dashboard, system] = await Promise.all([request('/dashboard'), api('/health').catch(() => null)])
      setData(dashboard)
      if (system) setHealth(system)
    } catch (error) {
      if (error.status === 401) { setToken(''); sessionStorage.removeItem('omnicart-admin') }
      else notify(error.message, true)
    }
  }, [request, notify])

  useEffect(() => {
    if (!token) return
    refresh()
    const timer = setInterval(refresh, 10000)
    return () => clearInterval(timer)
  }, [token, refresh])

  useEffect(() => {
    if (!token || tab !== 'customers' || !selected) return
    let active = true
    const poll = () => request(`/customers/${selected}/messages`).then(value => active && setMessages(value)).catch(error => active && notify(error.message, true))
    poll()
    const timer = setInterval(poll, 4000)
    return () => { active = false; clearInterval(timer) }
  }, [selected, tab, token, request, notify])

  async function login(event) {
    event.preventDefault(); setLoading(true)
    try {
      const result = await api('/admin/login', { method: 'POST', body: credentials })
      sessionStorage.setItem('omnicart-admin', result.token)
      setToken(result.token)
      setCredentials({ email: '', password: '' })
    } catch (error) { notify(error.message, true) }
    finally { setLoading(false) }
  }

  async function action(path, method = 'POST', body) {
    setLoading(true)
    try { const result = await request(path, { method, body }); await refresh(); return result }
    catch (error) { notify(error.message, true); return null }
    finally { setLoading(false) }
  }

  async function logout() {
    try { await api('/sessions/current', { token, method: 'DELETE' }) } catch { /* Clear local session regardless. */ }
    sessionStorage.removeItem('omnicart-admin')
    setToken(''); setData(null)
  }

  if (!token) return <div className="admin-login panel"><span className="large-icon"><ShieldCheck /></span><span className="eyebrow">OMNICART OPERATIONS</span><h1>Welcome back.</h1><p>Sign in with your shop administrator account.</p><form onSubmit={login}><label>Email address<input type="email" autoComplete="username" required value={credentials.email} onChange={event => setCredentials({ ...credentials, email: event.target.value })} /></label><label>Password<input type="password" autoComplete="current-password" maxLength={72} required value={credentials.password} onChange={event => setCredentials({ ...credentials, password: event.target.value })} /></label><button className="primary full-width" disabled={loading}>{loading ? 'Signing in…' : 'Sign in securely'}<ArrowRight size={16} /></button></form><small className="muted">bcrypt-protected administrator account · sessions expire automatically</small></div>

  if (!data) return <div className="admin-loading"><Activity className="spin" /><p>Loading operations workspace…</p></div>

  const customer = data.customers.find(item => item.customer_id === selected)
  const openCases = data.cases.filter(item => item.status !== 'Resolved').length
  const categories = ['All', ...new Set(data.products.map(item => item.category).filter(Boolean))]

  return <div className="admin-shell">
    <aside className="admin-rail">
      <a className="admin-brand" href="/admin" onClick={event => { event.preventDefault(); setTab('overview') }}>
        <span className="brand-icon"><ShoppingBag size={22} /></span>
        <span>omnicart<span className="brand-dot">.</span><small>OPERATIONS CONSOLE</small></span>
      </a>
      <span className="admin-rail-label">WORKSPACE</span>
      <nav className="admin-nav" aria-label="Admin navigation">
        {navigation.map(([key, Icon, label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => { setTab(key); setEditing(null); setSearch('') }}><Icon size={18} />{label}{key === 'support' && openCases > 0 && <em>{openCases}</em>}{tab === key && <ChevronRight size={14} className="nav-chevron" />}</button>)}
      </nav>
      <div className="admin-rail-bottom">
        <button className={tab === 'settings' ? 'admin-nav active' : 'admin-nav'} onClick={() => setTab('settings')}><Activity size={18} />System health<ChevronRight size={14} className="nav-chevron" /></button>
        <div className="admin-health"><span className={health ? 'live-dot' : 'live-dot offline'} /><div><strong>{health ? 'Store online' : 'Connecting…'}</strong><small>{health?.dense_status === 'ready' ? 'Hybrid RAG ready' : 'Lexical RAG active'}</small></div></div>
        <div className="admin-account"><span className="avatar">A</span><div><strong>Shop administrator</strong><small>Private workspace</small></div><button className="icon-button" aria-label="Admin logout" onClick={logout}><LogOut size={16} /></button></div>
      </div>
    </aside>
    <div className="admin-workspace">
      <header className="admin-topbar">
        <div className="admin-breadcrumb"><span>Operations</span><ChevronRight size={13} /><strong>{navigation.find(item => item[0] === tab)?.[2] || 'System health'}</strong></div>
        <div className="admin-top-actions"><button className="icon-button" aria-label="Refresh admin data" onClick={refresh}><RefreshCw size={17} /></button><span className="admin-date">{new Date().toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}</span></div>
      </header>
      <main className="admin-page">
        {tab === 'overview' && <Overview data={data} health={health} onNavigate={setTab} />}
        {tab === 'products' && <Catalogue data={data} categories={categories} category={category} setCategory={setCategory} search={search} setSearch={setSearch} loading={loading} action={action} notify={notify} editing={editing} setEditing={setEditing} />}
        {tab === 'orders' && <Orders data={data} search={search} setSearch={setSearch} loading={loading} action={action} />}
        {tab === 'customers' && <Customers data={data} selected={selected} setSelected={setSelected} messages={messages} setMessages={setMessages} reply={reply} setReply={setReply} customer={customer} loading={loading} action={action} search={search} setSearch={setSearch} />}
        {tab === 'support' && <SupportDesk request={request} notify={notify} cases={data.cases} refresh={refresh} />}
        {tab === 'knowledge' && <KnowledgeStudio request={request} notify={notify} />}
        {tab === 'policies' && <Policies data={data} notify={notify} loading={loading} action={action} editing={editing} setEditing={setEditing} />}
        {tab === 'audit' && <AuditTrail request={request} notify={notify} />}
        {tab === 'settings' && <Settings health={health} usage={data.ai_usage} loading={loading} action={action} />}
      </main>
    </div>
  </div>
}

function PageHeading({ eyebrow, title, description, children }) {
  return <div className="admin-page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{children && <div className="button-row">{children}</div>}</div>
}

function Overview({ data, health, onNavigate }) {
  const revenue = data.orders.filter(order => order.status !== 'Cancelled').reduce((sum, order) => sum + Number(order.total_price || 0), 0)
  const statuses = ['Pending', 'Processing', 'Shipped', 'Delivered', 'Cancelled'].map(status => [status, data.orders.filter(order => order.status === status).length])
  const maxStatus = Math.max(1, ...statuses.map(item => item[1]))
  const recent = data.orders.slice(0, 5)
  const lowStock = data.products.filter(product => product.stock_count <= 5).slice(0, 5)
  const openCases = data.cases.filter(item => item.status !== 'Resolved')
  const aiCost = Number(data.ai_usage?.cost_usd || 0)
  return <>
    <PageHeading eyebrow="OPERATIONS OVERVIEW" title="Your store, at a glance." description="Live commerce, catalogue, support and AI health in one workspace.">
      <button className="secondary" onClick={() => onNavigate('orders')}>View orders <ArrowRight size={14} /></button>
      <button className="primary" onClick={() => onNavigate('support')}>Support cases <ArrowRight size={14} /></button>
    </PageHeading>
    <div className="admin-kpis">
      <Metric icon={CircleDollarSign} label="Revenue" value={currency(revenue)} note="Non-cancelled orders" />
      <Metric icon={ShoppingBag} label="Orders" value={data.metrics.orders} note={`${data.orders.filter(order => order.status === 'Pending').length} pending`} />
      <Metric icon={Users} label="Customers" value={data.metrics.customers} note="Private guest sessions" />
      <Metric icon={MessageSquare} label="Open cases" value={data.metrics.open_cases} note={openCases.length ? 'Needs a human' : 'All clear'} accent={data.metrics.open_cases > 0} />
      <Metric icon={Activity} label="AI cost" value={formatCost(aiCost)} note={`${data.ai_usage?.calls || 0} model calls · estimated`} />
    </div>
    <div className="admin-grid-2">
      <section className="panel admin-chart"><div className="admin-card-head"><div><span className="eyebrow">ORDER PIPELINE</span><h3>Where orders stand</h3></div><BarChart3 size={19} /></div><div className="status-chart">{statuses.map(([status, count]) => <div className="status-bar" key={status}><span>{status}</span><div><i className={status.toLowerCase()} style={{ width: `${Math.max(6, count / maxStatus * 100)}%` }} /></div><strong>{count}</strong></div>)}</div></section>
      <section className="panel admin-chart"><div className="admin-card-head"><div><span className="eyebrow">RETRIEVAL SYSTEM</span><h3>AI knowledge health</h3></div><Activity size={19} /></div><div className="rag-health"><div><span className={health?.dense_status === 'ready' ? 'health-orb ready' : 'health-orb'} /><div><strong>{health?.dense_status === 'ready' ? 'Hybrid RAG active' : 'Lexical RAG active'}</strong><small>{health?.dense_status === 'ready' ? 'BM25 + multilingual dense retrieval' : 'Dense index warming or unavailable'}</small></div></div><div className="rag-stats"><span><small>Products</small><strong>{health?.products ?? '—'}</strong></span><span><small>Policies</small><strong>{health?.policies ?? '—'}</strong></span><span><small>MCP database</small><strong>{health?.mcp_enabled ? 'MCP' : 'Local'}</strong></span></div><button className="secondary" onClick={() => onNavigate('knowledge')}>Open knowledge studio <ArrowRight size={14} /></button></div></section>
    </div>
    <div className="admin-grid-2">
      <EscalationAlerts alerts={data.escalation_alerts || []} onNavigate={onNavigate} />
      <SentimentPanel sentiment={data.sentiment} />
    </div>
    <div className="admin-grid-2">
      <section className="panel admin-table-card"><div className="admin-card-head"><div><span className="eyebrow">LATEST ACTIVITY</span><h3>Recent orders</h3></div><button className="text-button" onClick={() => onNavigate('orders')}>All orders</button></div><TableHead columns={['Order', 'Customer', 'Total', 'Status']} />{recent.map(order => <div className="admin-row" key={order.order_id}><span><strong>{order.reference}</strong><small>{order.product_name}</small></span><span>#{order.customer_id}</span><span><strong>{currency(order.total_price)}</strong></span><span className={`status status-${order.status.toLowerCase()}`}>{order.status}</span></div>)}{!recent.length && <p className="admin-empty">No orders yet.</p>}</section>
      <section className="panel admin-table-card"><div className="admin-card-head"><div><span className="eyebrow">ATTENTION NEEDED</span><h3>Low stock</h3></div><button className="text-button" onClick={() => onNavigate('products')}>Catalogue</button></div><TableHead columns={['Product', 'Category', 'Stock']} />{lowStock.map(product => <div className="admin-row" key={product.product_id}><span><strong>{product.name}</strong><small>{product.product_id}</small></span><span>{product.category}</span><span className={product.stock_count ? 'stock-ok' : 'stock-warning'}>{product.stock_count}</span></div>)}{!lowStock.length && <p className="admin-empty">Healthy stock levels.</p>}</section>
    </div>
  </>
}

const MOODS = [['POSITIVE', 'Positive'], ['NEUTRAL', 'Neutral'], ['NEGATIVE', 'Negative'], ['FRUSTRATED', 'Frustrated']]

function EscalationAlerts({ alerts, onNavigate }) {
  return <section className="panel admin-table-card escalation-feed">
    <div className="admin-card-head"><div><span className="eyebrow">HUMAN HANDOVER</span><h3>Escalation alerts</h3></div><button className="text-button" onClick={() => onNavigate('support')}>Support desk</button></div>
    {alerts.slice(0, 6).map(alert => <article className={`escalation-alert ${alert.priority === 'urgent' ? 'urgent' : ''}`} key={alert.case_id}>
      <header>
        <strong>Case #{alert.case_id} · {alert.trigger}</strong>
        <span className={`mood mood-${(alert.sentiment || 'NEUTRAL').toLowerCase()}`}>{alert.sentiment}</span>
      </header>
      <p className="escalation-last">“{alert.last_message}”</p>
      <small>
        Customer #{alert.customer_id} via {alert.channel}
        {alert.products?.length ? ` · ${alert.products.map(product => product.name).join(', ')}` : ''}
        {alert.image_context?.description ? ` · photo: ${alert.image_context.description}` : ''}
      </small>
      {alert.conversation_summary && <small className="escalation-summary">{alert.conversation_summary}</small>}
    </article>)}
    {!alerts.length && <p className="admin-empty">No conversations waiting for a person.</p>}
  </section>
}

function SentimentPanel({ sentiment }) {
  const counts = sentiment?.counts || {}
  const total = Math.max(1, Object.values(counts).reduce((sum, value) => sum + value, 0))
  return <section className="panel admin-chart">
    <div className="admin-card-head"><div><span className="eyebrow">CUSTOMER MOOD · {sentiment?.window_days || 7} DAYS</span><h3>Sentiment</h3></div><TrendingUp size={19} /></div>
    <div className="status-chart">{MOODS.map(([key, label]) => <div className="status-bar" key={key}><span>{label}</span><div><i className={`mood-bar mood-${key.toLowerCase()}`} style={{ width: `${Math.max(4, (counts[key] || 0) / total * 100)}%` }} /></div><strong>{counts[key] || 0}</strong></div>)}</div>
    <div className="rag-stats"><span><small>Average score</small><strong>{sentiment?.average_score ?? '—'}</strong></span><span><small>Escalated now</small><strong>{sentiment?.escalated_conversations ?? 0}</strong></span></div>
  </section>
}

function Metric({ icon: Icon, label, value, note, accent }) {
  return <div className={`admin-kpi ${accent ? 'accent' : ''}`}><div className="kpi-icon"><Icon size={18} /></div><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

function TableHead({ columns }) {
  return <div className="admin-row admin-table-head">{columns.map(column => <span key={column}>{column}</span>)}</div>
}

function Catalogue({ data, categories, category, setCategory, search, setSearch, loading, action, notify, editing, setEditing }) {
  const filtered = data.products.filter(product => (category === 'All' || product.category === category) && `${product.name} ${product.product_id} ${product.category}`.toLowerCase().includes(search.toLowerCase()))
  async function addAlias(product) {
    const alias = window.prompt(`Add a search alias for ${product.name}`)
    if (!alias || alias.trim().length < 2) return
    const result = await action(`/products/${encodeURIComponent(product.product_id)}/aliases`, 'POST', { alias: alias.trim(), locale: 'bn-Latn' })
    if (result) notify('Search alias added. Retrieval can use it immediately.')
  }
  async function setImage(product) {
    const imageUrl = window.prompt(`Real image URL for ${product.name}\nLeave blank to restore the generated illustration.`, '')
    if (imageUrl === null) return
    const result = await action(`/products/${encodeURIComponent(product.product_id)}/image`, 'PUT', { image_url: imageUrl.trim() || null, alt_text: product.name })
    if (result) notify(imageUrl.trim() ? 'Product image URL saved.' : 'Product image reset to the generated illustration.')
  }
  return <>
    <PageHeading eyebrow="CATALOGUE & INVENTORY" title="Everything you sell." description="Edit live prices, stock, variants and search aliases. Changes are read by RAG immediately." />
    <div className="admin-toolbar"><div className="admin-search"><Search size={17} /><input aria-label="Search catalogue" placeholder="Search product, SKU or category…" value={search} onChange={event => setSearch(event.target.value)} /></div><select aria-label="Filter catalogue category" value={category} onChange={event => setCategory(event.target.value)}>{categories.map(item => <option key={item}>{item}</option>)}</select><button className="primary" onClick={() => setEditing({ product_id: '', name: '', category: '', price: 0, discounted_price: null, stock_count: 0, colors: [], sizes: [], description: '' })}><Plus size={15} />Add product</button><button className="secondary" disabled={loading} onClick={async () => { const result = await action('/knowledge/refresh'); if (result) notify(`Knowledge index refreshed. Dense status: ${result.dense_status}.`) }}><RefreshCw size={14} />Refresh RAG</button></div>
    {editing && <Editor key={editing.product_id || 'new-product'} record={editing} kind="products" loading={loading} onCancel={() => setEditing(null)} onSave={async record => { const result = await action(`/products/${encodeURIComponent(record.product_id)}`, 'PUT', record); if (result) { setEditing(null); notify('Product saved. Live retrieval reads the updated row.') } }} />}
    <div className="admin-table panel"><TableHead columns={['Product', 'Category', 'Price', 'Stock', '']} />{filtered.map(product => <div className="admin-row" key={product.product_id}><span><strong>{product.name}</strong><small>{product.product_id}</small></span><span>{product.category}</span><span><strong>{currency(product.effective_price)}</strong>{product.discounted_price != null && product.discounted_price < product.price && <small className="price-cut">was {currency(product.price)}</small>}</span><span className={product.stock_count ? 'stock-ok' : 'stock-warning'}>{product.stock_count}</span><span className="row-actions"><button className="text-button" onClick={() => setEditing(product)}>Edit</button><button className="text-button" onClick={() => addAlias(product)}>Alias</button><button className="text-button" onClick={() => setImage(product)}>Image</button></span></div>)}{!filtered.length && <p className="admin-empty">No matching products.</p>}</div>
  </>
}

function Orders({ data, search, setSearch, loading, action }) {
  const [status, setStatus] = useState('All')
  const filtered = data.orders.filter(order => (status === 'All' || order.status === status) && `${order.reference} ${order.product_name} ${order.customer_id}`.toLowerCase().includes(search.toLowerCase()))
  return <>
    <PageHeading eyebrow="ORDERS & FULFILMENT" title="Every order, accounted for." description="Inspect live orders and move them through the fulfilment states with server-side validation." />
    <div className="admin-toolbar"><div className="admin-search"><Search size={17} /><input aria-label="Search orders" placeholder="Search order, product or customer…" value={search} onChange={event => setSearch(event.target.value)} /></div><select aria-label="Filter order status" value={status} onChange={event => setStatus(event.target.value)}>{['All', 'Pending', 'Processing', 'Shipped', 'Delivered', 'Cancelled'].map(item => <option key={item}>{item}</option>)}</select><span className="admin-toolbar-note">{filtered.length} orders</span></div>
    <div className="admin-table panel"><TableHead columns={['Order', 'Product', 'Qty', 'Total', 'Status']} />{filtered.map(order => <div className="admin-row" key={order.order_id}><span><strong>{order.reference}</strong><small>Customer #{order.customer_id} · {new Date(order.created_at).toLocaleDateString()}</small></span><span>{order.product_name}</span><span>{order.quantity}</span><span><strong>{currency(order.total_price)}</strong></span><span><select aria-label={`Status of ${order.reference}`} value={order.status} disabled={loading} onChange={event => action(`/orders/${order.order_id}`, 'PATCH', { status: event.target.value })}>{[order.status, ...({ Pending: ['Processing', 'Cancelled'], Processing: ['Shipped', 'Cancelled'], Shipped: ['Delivered'] }[order.status] || [])].map(item => <option key={item}>{item}</option>)}</select></span></div>)}{!filtered.length && <p className="admin-empty">No orders in this view.</p>}</div>
  </>
}

function Customers({ data, selected, setSelected, messages, setMessages, reply, setReply, customer, loading, action, search, setSearch }) {
  const filtered = data.customers.filter(item => `${item.name || ''} ${item.email || ''} ${item.phone || ''} ${item.customer_id}`.toLowerCase().includes(search.toLowerCase()))
  return <>
    <PageHeading eyebrow="CUSTOMER CARE" title="People, not tickets." description="See the full conversation and step in exactly when a customer needs a human." />
    <div className="admin-chat-layout"><div className="customer-list panel"><div className="admin-search compact"><Search size={15} /><input aria-label="Search customers" placeholder="Search customer…" value={search} onChange={event => setSearch(event.target.value)} /></div>{filtered.map(item => <button className={selected === item.customer_id ? 'selected' : ''} key={item.customer_id} onClick={() => { setSelected(item.customer_id); setMessages([]) }}><span className="avatar">{item.name?.slice(0, 1) || 'G'}</span><span><strong>{item.name || `Guest #${item.customer_id}`}</strong><small>{item.email || item.phone || 'Private guest session'}</small><em>{item.human_intervention ? 'Human support active' : 'AI assistant active'}</em></span></button>)}{!filtered.length && <p className="admin-empty">No matching customers.</p>}</div><div className="panel admin-conversation">{!customer ? <div className="empty-state"><MessageSquare size={30} /><p>Select a customer to view their conversation.</p></div> : <><header><div><strong>{customer.name || `Customer #${selected}`}</strong><small>{customer.human_intervention ? 'You can reply to this conversation' : 'Take over to send a human reply'}</small></div><button className={customer.human_intervention ? 'secondary' : 'primary'} disabled={loading} onClick={() => action(`/customers/${selected}/handoff`, 'PATCH', { enabled: !customer.human_intervention })}>{customer.human_intervention ? 'Hand back to AI' : 'Take over chat'}</button></header><div className="admin-messages">{messages.map(message => <div className={`admin-message ${message.sender}`} key={message.id}><small>{message.sender === 'user' ? 'Customer' : message.sender === 'admin' ? 'Support team' : 'AI assistant'}</small><p>{message.content}</p></div>)}{!messages.length && <p className="muted">No messages yet.</p>}</div><form onSubmit={async event => { event.preventDefault(); const result = await action(`/customers/${selected}/messages`, 'POST', { message: reply }); if (result) { setMessages(previous => [...previous, result]); setReply('') } }}><input aria-label="Reply to customer" placeholder={customer.human_intervention ? 'Write a human reply…' : 'Take over the conversation first'} disabled={!customer.human_intervention || loading} required value={reply} onChange={event => setReply(event.target.value)} /><button className="primary" disabled={!customer.human_intervention || loading || !reply.trim()}>Send</button></form></>}</div></div>
  </>
}

function Policies({ data, notify, loading, action, editing, setEditing }) {
  return <>
    <PageHeading eyebrow="STORE POLICY" title="The rules behind every answer." description="Policy documents are quoted verbatim where possible and versioned in the knowledge studio." />
    <div className="policy-list">{data.policies.map(policy => <article className="panel admin-policy" key={policy.policy_id}><div><span className="eyebrow">policy:{policy.policy_id}</span><h3>{policy.policy_type}</h3><p>{policy.policy_text}</p></div><button className="text-button" onClick={() => setEditing({ ...policy })}>Edit</button></article>)}</div>
    {editing && <Editor key={editing.policy_id || 'new-policy'} record={editing} kind="policies" loading={loading} onCancel={() => setEditing(null)} onSave={async record => { const result = await action(record.policy_id ? `/policies/${record.policy_id}` : '/policies', record.policy_id ? 'PUT' : 'POST', record); if (result) { setEditing(null); notify('Policy saved. Retrieval reads the updated source.') } }} />}
  </>
}

function Settings({ health, usage, loading, action }) {
  const models = Object.values(usage?.by_model || {})
  return <>
    <PageHeading eyebrow="SYSTEM HEALTH" title="Know what the assistant can see." description="Runtime configuration, retrieval status and estimated provider spend. Secrets are never displayed here." />
    <div className="admin-grid-2"><section className="panel admin-settings"><div className="admin-card-head"><div><span className="eyebrow">RAG &amp; DATABASE</span><h3>Assistant runtime</h3></div><Activity size={19} /></div><div className="settings-list">{[['Shop', health?.shop_name], ['Products indexed', health?.products], ['Policies indexed', health?.policies], ['Dense retrieval', health?.dense_status], ['Chat model', health?.chat_model], ['DeepSeek complex tier', health?.deepseek_configured ? 'Configured' : 'Not configured'], ['Vision model', health?.vision_model], ['Voice transcription', health?.stt_model], ['Embedding model', health?.embedding_model], ['MCP database transport', health?.mcp_enabled ? 'Enabled' : 'Local gateway'], ['Implementation', health?.implementation_phase]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value ?? '—'}</strong></div>)}</div></section><section className="panel admin-settings"><div className="admin-card-head"><div><span className="eyebrow">TOKEN ECONOMICS</span><h3>Estimated AI spend</h3></div><CircleDollarSign size={19} /></div><div className="cost-hero"><strong>{formatCost(usage?.cost_usd)}</strong><span>Across {usage?.calls || 0} recorded model calls</span></div><div className="cost-stats">{[['Input', usage?.input_tokens], ['Output', usage?.output_tokens], ['Cached input', usage?.cached_tokens]].map(([label, value]) => <div key={label}><small>{label}</small><strong>{Number(value || 0).toLocaleString()}</strong></div>)}</div>{models.length > 0 && <div className="cost-models">{models.map(model => <div key={`${model.provider}:${model.model}`}><span><strong>{model.provider}</strong><small>{model.model}</small></span><span>{Number(model.total_tokens || 0).toLocaleString()} tok</span><strong>{formatCost(model.cost_usd)}</strong></div>)}</div>}<p className="admin-muted">Estimates use configurable per-million-token rates and may differ from provider invoices, especially DeepSeek peak/off-peak pricing.</p></section></div>
    <section className="panel admin-settings"><div className="admin-card-head"><div><span className="eyebrow">OPERATIONS</span><h3>Safe maintenance</h3></div><RefreshCw size={19} /></div><p className="admin-muted">Rebuild the retrieval index after bulk catalogue changes, or retry queued notification delivery.</p><div className="settings-actions"><button className="secondary" disabled={loading} onClick={() => action('/knowledge/refresh')}>Refresh RAG index</button><button className="secondary" disabled={loading} onClick={() => action('/notifications/retry')}>Retry notifications</button></div><div className="settings-note"><ShieldCheck size={16} /><p>Production release gates still require PostgreSQL/pgvector, Redis queues, 2FA/SSO, four-eyes approvals, payment/courier webhooks and external channel certification.</p></div></section>
  </>
}

function Editor({ record, kind, onSave, onCancel, loading }) {
  const [form, setForm] = useState(record)
  const isProduct = kind === 'products'
  const fields = isProduct ? [['product_id', 'Product ID', 'text'], ['name', 'Product name', 'text'], ['category', 'Category', 'text'], ['price', 'List price', 'number'], ['discounted_price', 'Discounted price (optional)', 'number'], ['stock_count', 'Stock count', 'number'], ['colors', 'Colors (comma separated)', 'list'], ['sizes', 'Sizes (comma separated)', 'list']] : [['policy_type', 'Policy title', 'text']]
  return <form className="panel editor" onSubmit={event => { event.preventDefault(); onSave(form) }}><div className="editor-heading"><h3>{record.product_id || record.policy_id ? 'Edit record' : 'Add a record'}</h3><button className="icon-button" type="button" aria-label="Cancel edit" onClick={onCancel}><X size={18} /></button></div><div className="form-grid">{fields.map(([key, label, type]) => <label key={key}>{label}<input type={type === 'number' ? 'number' : 'text'} min={0} step={key === 'stock_count' ? 1 : 'any'} required={key !== 'discounted_price' && type !== 'list'} disabled={key === 'product_id' && !!record.product_id} value={type === 'list' ? form[key].join(',') : form[key] ?? ''} onChange={event => setForm({ ...form, [key]: type === 'list' ? event.target.value.split(',').map(value => value.trim()) : type === 'number' ? event.target.value === '' && key === 'discounted_price' ? null : Number(event.target.value) : event.target.value })} /></label>)}</div><label>{isProduct ? 'Description' : 'Policy text'}<textarea required={!isProduct} rows={4} value={isProduct ? form.description : form.policy_text} onChange={event => setForm({ ...form, [isProduct ? 'description' : 'policy_text']: event.target.value })} /></label><button className="primary" disabled={loading}><Save size={15} />{loading ? 'Saving…' : 'Save changes'}</button></form>
}
