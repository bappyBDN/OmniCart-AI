import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { ArrowRight, ArrowUp, ArrowUpRight, BookOpen, Bot, CheckCheck, ChevronDown, CircleHelp, Clock3, FileCheck2, Headset, ImagePlus, Layers3, LoaderCircle, Mic, Package, Search, ShieldCheck, Sparkles, Square, X } from 'lucide-react'
import { currency } from './api'
import { productPath } from './productLinks'

const starters = [
  { icon: Search, title: 'Find something you’ll love', text: 'Show me cotton shirts under $30', color: 'green' },
  { icon: Package, title: 'The details, without the guesswork', text: 'What is your shipping policy?', color: 'orange' },
  { icon: CircleHelp, title: 'A little help goes a long way', text: 'What is the difference between returns and refunds?', color: 'blue' },
]

export default function Chat({ request, health, profile, setProfile, notify, onCheckout, onNavigate }) {
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [image, setImage] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [lastResult, setLastResult] = useState(null)
  const [drafts, setDrafts] = useState([])
  const [retry, setRetry] = useState(null)
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [recordSeconds, setRecordSeconds] = useState(0)
  const fileRef = useRef(null)
  const endRef = useRef(null)
  const textRef = useRef(null)
  const recorderRef = useRef(null)
  const streamRef = useRef(null)
  const chunksRef = useRef([])

  useEffect(() => {
    let active = true
    async function load() {
      try {
        const [history, customer, pending] = await Promise.all([request('/messages'), request('/profile'), request('/checkout')])
        if (!active) return
        setMessages(history); setProfile(customer); setDrafts(pending)
        const last = [...history].reverse().find(message => message.metadata)
        if (last) setLastResult(last.metadata)
      } catch (error) { if (active) notify(error.message, true) }
      finally { if (active) setLoading(false) }
    }
    if (!busy) load()
    const timer = setInterval(() => { if (!busy) load() }, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [request, busy, notify, setProfile])

  useEffect(() => {
    const container = endRef.current?.parentElement
    container?.scrollTo?.({ top: container.scrollHeight, behavior: 'smooth' })
  }, [messages.length, busy])

  useEffect(() => {
    if (!recording) return
    setRecordSeconds(0)
    const timer = setInterval(() => setRecordSeconds(value => Math.min(120, value + 1)), 1000)
    return () => clearInterval(timer)
  }, [recording])

  useEffect(() => () => {
    recorderRef.current?.stop?.()
    streamRef.current?.getTracks?.().forEach(track => track.stop())
  }, [])

  async function send(value = text, retryPayload = null) {
    if (busy || (!value.trim() && !image)) return
    const payload = retryPayload || { message: value.trim() || 'Find a product like this image.', image: image?.data || null, request_id: crypto.randomUUID() }
    setBusy(true); setRetry(null)
    setMessages(previous => [...previous.filter(m => !m.optimistic), { id: `temp-${payload.request_id}`, sender: 'user', content: payload.message, optimistic: true }])
    setText(''); setImage(null)
    try {
      const response = await request('/chat', { method: 'POST', body: payload })
      setLastResult(response)
      if (response.drafts?.length) setDrafts(response.drafts)
      const [history, customer, pending] = await Promise.all([request('/messages'), request('/profile'), request('/checkout')])
      setMessages(history); setProfile(customer); setDrafts(pending)
    } catch (error) {
      notify(error.message, true); setRetry(payload)
      setMessages(previous => previous.filter(m => !m.optimistic))
    } finally { setBusy(false); textRef.current?.focus() }
  }

  async function upload(event) {
    const file = event.target.files?.[0]
    if (!file) return
    event.target.value = ''
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 3000000) { notify('Use a PNG, JPEG, or WebP image up to 3 MB.', true); return }
    const reader = new FileReader()
    reader.onload = () => setImage({ data: reader.result, name: file.name })
    reader.onerror = () => notify('Could not read the image.', true)
    reader.readAsDataURL(file)
  }

  async function startVoice() {
    if (recording || transcribing || busy) return
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      notify('Voice notes are not supported by this browser. Please type your message.', true)
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(type => MediaRecorder.isTypeSupported(type)) || ''
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []
      recorder.ondataavailable = event => { if (event.data.size) chunksRef.current.push(event.data) }
      recorder.onstop = async () => {
        streamRef.current?.getTracks().forEach(track => track.stop())
        streamRef.current = null
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        if (!blob.size || blob.size > 16 * 1024 * 1024) {
          notify(blob.size ? 'Voice note is too large. Keep it under 16 MB.' : 'No audio was recorded.', true)
          return
        }
        const reader = new FileReader()
        reader.onload = async () => {
          setTranscribing(true)
          try {
            const result = await request('/voice/transcribe', { method: 'POST', body: { audio: reader.result } })
            setText(previous => previous ? `${previous} ${result.text}` : result.text)
            notify('Voice note transcribed. Review it, then send.')
            textRef.current?.focus()
          } catch (error) { notify(error.message, true) }
          finally { setTranscribing(false) }
        }
        reader.onerror = () => notify('Could not read the recording.', true)
        reader.readAsDataURL(blob)
      }
      streamRef.current = stream
      recorderRef.current = recorder
      recorder.start()
      setRecording(true)
    } catch (error) {
      notify('Microphone permission was not granted.', true)
    }
  }

  function stopVoice() {
    if (!recording) return
    setRecording(false)
    recorderRef.current?.stop()
  }

  async function support() {
    try {
      await request('/support', { method: 'POST', body: { message: 'Customer requested a human from the chat interface.' } })
      setProfile(await request('/profile')); notify('Support requested. An agent can now join this conversation.')
    } catch (error) { notify(error.message, true) }
  }

  return <div className="chat-layout"><section className="chat-main"><div className="assistant-heading"><div><span className="eyebrow"><span className="live-dot" /> YOUR EVERYDAY SHOPPING COMPANION</span><h1>A good conversation.<br />An even better find<span>.</span></h1><p>Ask, explore, and shop — with a little intelligence on your side.</p></div><span className="assistant-decoration"><Bot size={43} strokeWidth={1.25} /><Sparkles size={18} /></span></div>
    <div className="chat-panel"><header className="chat-panel-header"><span className="bot-avatar"><Bot size={21} /></span><div><strong>OmniCart assistant <span className="beta-tag">AI</span></strong><small><i className="live-dot" />{profile?.human_intervention ? 'Human support requested' : 'Ready when you are'}</small></div><span className="language-tag">EN / বাংলা</span></header>
      <div className={`message-scroll ${messages.length ? 'has-messages' : ''}`} aria-live="polite" aria-busy={busy || loading}>
        {loading ? <div className="loading"><LoaderCircle size={20} className="spin" />Loading your conversation…</div> : !messages.length ? <div className="conversation-welcome"><span className="welcome-spark"><Sparkles size={25} /></span><h2>Hi{profile?.name ? `, ${profile.name}` : ' there'}! What’s on your mind?</h2><p>I can help you discover products, understand store policies,<br />or get a hand with your order. Where shall we start?</p><div className="starter-grid">{starters.map(({ icon: Icon, title, text: prompt, color }) => <button className="starter-card" key={title} onClick={() => send(prompt)}><span className={`starter-icon ${color}`}><Icon size={18} /></span><strong>{title}</strong><span>{prompt}</span><ArrowUpRight size={15} className="starter-arrow" /></button>)}</div><span className="welcome-hint">Or just say হ্যালো. We speak your language.</span></div> : messages.map(message => <article key={message.id} className={`message ${message.sender === 'user' ? 'user' : message.sender === 'admin' ? 'admin' : 'assistant'}`}><span className={`message-avatar ${message.sender === 'admin' ? 'human' : ''}`}>{message.sender === 'user' ? (profile?.name?.slice(0, 1) || 'Y') : message.sender === 'admin' ? <Headset size={17} /> : <Bot size={18} />}</span><div className="message-body"><span className="message-label">{message.sender === 'user' ? 'You' : message.sender === 'admin' ? 'Support team' : 'OmniCart'}{message.timestamp && <time>{new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>}</span><div className="message-text"><ReactMarkdown allowedElements={['p', 'strong', 'em', 'ul', 'ol', 'li', 'code', 'pre', 'blockquote', 'a', 'br', 'h2', 'h3', 'hr']} components={{ a: ({ children }) => <span>{children}</span> }}>{message.content}</ReactMarkdown></div>{message.metadata?.show_images && message.metadata?.sources?.filter(source => source.kind === 'product' && source.data?.image_url).length > 0 && <ProductCards sources={message.metadata.sources} onNavigate={onNavigate} />}{message.metadata?.sources?.length > 0 && <SourceList sources={message.metadata.sources} />}{message.metadata?.degraded && <span className="fallback-note"><CircleHelp size={12} />Limited AI availability · verified records shown</span>}</div></article>)}
        {busy && <div className="thinking"><span className="bot-avatar"><Bot size={18} /></span><div><strong>{profile?.human_intervention ? 'Sending to support' : 'Checking the evidence'}</strong><span className="thinking-dots"><i /><i /><i /></span></div></div>}
        <div ref={endRef} />
      </div>
      {profile?.human_intervention && <div className="handoff-banner"><Headset size={17} />Human support is active. Your messages will go to the team.</div>}
      {lastResult?.offer_handoff && !profile?.human_intervention && <div className="handoff-offer"><span>I don't want to guess. Would you like the team to check this?</span><button className="secondary" onClick={support} disabled={busy}>Ask a human</button></div>}
      {drafts.length > 0 && <div className="draft-list">{drafts.map(draft => <button className="draft-chip" key={draft.draft_id} onClick={() => onCheckout(draft)}><ShoppingIcon /><span>Review {draft.quantity} × {draft.product_name}<small>{currency(draft.total_price)} subtotal · confirmation required</small></span><ArrowRight size={17} /></button>)}</div>}
      {retry && <div className="retry-banner">Message not completed.<button onClick={() => send(retry.message, retry)}>Retry safely</button><button onClick={() => { setText(retry.message); setRetry(null) }}>Edit message</button></div>}
      <form className="composer" onSubmit={event => { event.preventDefault(); send() }}>{image && <div className="attachment"><img src={image.data} alt="Attached product" /><span>{image.name}</span><button type="button" className="icon-button" aria-label="Remove image" onClick={() => setImage(null)}><X size={15} /></button></div>}<div className="composer-input"><textarea ref={textRef} aria-label="Message OmniCart" placeholder="Ask about a product, a policy, or anything shopping…" rows={1} maxLength={4000} value={text} disabled={busy} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send() } }} /><button type="submit" className="send-button" aria-label="Send message" disabled={busy || (!text.trim() && !image)}>{busy ? <LoaderCircle size={19} className="spin" /> : <ArrowUp size={20} />}</button></div><div className="composer-bottom"><button type="button" className="attach-button" onClick={() => fileRef.current?.click()} disabled={busy || recording || transcribing}><ImagePlus size={16} />Add a photo</button><button type="button" className={`attach-button voice-button ${recording ? 'recording' : ''}`} onClick={recording ? stopVoice : startVoice} disabled={busy || transcribing}>{transcribing ? <LoaderCircle size={15} className="spin" /> : recording ? <Square size={14} /> : <Mic size={15} />}{transcribing ? 'Transcribing…' : recording ? `Stop ${String(Math.floor(recordSeconds / 60)).padStart(2, '0')}:${String(recordSeconds % 60).padStart(2, '0')}` : 'Voice note'}</button><span><ShieldCheck size={12} />Grounded in your store’s data</span><span className="enter-hint">Enter to send ↵</span></div><input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="visually-hidden" onChange={upload} /></form>
    </div><p className="chat-disclaimer"><Sparkles size={12} />AI can make mistakes. Check cited sources and review your order before confirming.</p>
  </section><aside className="insights"><div className="insight-heading"><Layers3 size={18} /><strong>Behind the answer</strong><span className="live-pill">LIVE</span></div><p className="insight-intro">Not just an answer.<br />A little transparency, too.</p><div className="knowledge-card"><div className="knowledge-icon"><BookOpen size={20} /></div><strong>Connected to your store</strong><p>Your assistant looks up real information before answering.</p><div className="knowledge-stat"><span>Products in catalog</span><strong>{health?.products ?? '—'}</strong></div><div className="knowledge-stat"><span>Store policies</span><strong>{health?.policies ?? '—'}</strong></div><div className="knowledge-stat"><span>Retrieval mode</span><span className="retrieval-badge">{health?.dense_status === 'ready' ? 'Hybrid' : health?.dense_status === 'indexing' ? 'Lexical · warming' : 'Lexical'}</span></div></div><div className="process-card"><span className="eyebrow">THE AGENT WORKFLOW</span>{[['Understand your request', 'Route to the right workflow'], ['Find the right information', 'Retrieve live, relevant evidence'], ['Use the right tools', 'Search, profile, checkout, support'], ['Check before answering', 'Validate facts and citations']].map(([title, detail], index) => <div className={`process-step ${lastResult ? 'completed' : ''}`} key={title}><span>{lastResult ? <CheckCheck size={13} /> : String(index + 1).padStart(2, '0')}</span><div><strong>{title}</strong><small>{detail}</small></div></div>)}</div>{lastResult && <details className="trace-panel"><summary>Last request activity<ChevronDown size={14} /></summary>{lastResult.trace.map((event, index) => <div className="trace-event" key={index}><span>{event.node}</span><small>{event.detail}</small></div>)}<div className="trace-summary"><Clock3 size={12} />{(lastResult.latency_ms / 1000).toFixed(1)}s <span>{lastResult.usage?.total_tokens || 0} agent tokens</span></div></details>}<div className="support-card"><Headset size={23} /><strong>Sometimes, a human helps.</strong><p>Our team can pick up right where the assistant left off.</p><button disabled={profile?.human_intervention || busy} onClick={support}>{profile?.human_intervention ? 'Support requested' : 'Talk to the team'}<ArrowUpRight size={14} /></button></div><button className="profile-nudge" onClick={() => onNavigate('profile')}><ShieldCheck size={16} /><span>{profile?.missing?.length ? 'Complete your profile for easy checkout' : 'Your checkout profile is ready'}</span><ArrowRight size={14} /></button></aside></div>
}

function ShoppingIcon() { return <Package size={20} /> }

export function ProductCards({ sources, onNavigate }) {
  const products = (sources || []).filter(source => source.kind === 'product' && source.data).slice(0, 4)
  if (!products.length) return null
  return <div className="product-cards">{products.map(source => { const stock = source.data.stock_count; const stockLabel = stock === null || stock === undefined ? 'Stock needs confirmation' : stock > 0 ? `${stock} in stock` : 'Out of stock'; return <article className="chat-product" key={source.id}><div className="chat-product-image"><img src={source.data.image_url} alt={`${source.title} product image`} loading="lazy" /><span>{source.data.category} representative image</span></div><div className="chat-product-body"><strong>{source.data.name}</strong><p>{currency(source.data.effective_price)} · {stockLabel}</p><small>{source.graph?.relation === 'alternative' ? 'Similar in-stock alternative' : 'Similar catalogue match'}</small><a className="text-button" href={productPath(source.data.product_id || source.id.replace(/^product:/, ''))}>View product <ArrowRight size={12} /></a></div></article> })}</div>
}

export function SourceList({ sources }) {
  return <details className="sources"><summary><FileCheck2 size={13} />{sources.length} retrieved source{sources.length === 1 ? '' : 's'}<ChevronDown size={12} /></summary><div className="source-items">{sources.map(source => <article key={source.id}><span className="eyebrow">{source.kind} · {source.id}{source.retrieval ? ` · ${source.retrieval}` : ''}{typeof source.confidence === 'number' ? ` · ${Math.round(source.confidence * 100)}%` : ''}</span><strong>{source.title}</strong><p>{source.kind === 'product' && source.data ? `${currency(source.data.effective_price)} · ${source.data.stock_count ?? 'stock needs confirmation'} in stock · ${source.data.description}` : source.content}</p></article>)}</div></details>
}
