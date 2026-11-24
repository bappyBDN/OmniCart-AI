import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ShoppingBag, X } from 'lucide-react'
import Admin from './Admin'
import './styles.css'
import './operations.css'
import './admin.css'

function AdminApplication() {
  const [notice, setNotice] = useState(null)
  const notify = React.useCallback((message, error = false) => setNotice({ message, error }), [])
  return <div className="admin-application"><header><a className="brand" href="/admin"><span className="brand-icon"><ShoppingBag size={22} /></span><span>omnicart<span className="brand-dot">.</span><small>OPERATIONS CONSOLE</small></span></a><a href="/">Customer storefront ↗</a></header><main><Admin notify={notify} /></main>{notice && <div className={`toast ${notice.error ? 'error' : ''}`} role="status"><span>{notice.message}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice(null)}><X size={16} /></button></div>}</div>
}

createRoot(document.getElementById('root')).render(<React.StrictMode><AdminApplication /></React.StrictMode>)
