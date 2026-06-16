import React, { useState, useEffect, useRef, useCallback } from 'react'

// ─── CONSTANTS ────────────────────────────────────────────────────────────────

const PLATFORMS = {
  robinhood: { name: 'Robinhood', color: '#00C805', types: ['stock', 'option'] },
  kraken:    { name: 'Kraken',    color: '#5741D9', types: ['crypto'] },
  phantom:   { name: 'Phantom',   color: '#AB9FF2', types: ['crypto', 'memecoin'] },
  dub:       { name: 'Dub',       color: '#0EA5E9', types: ['copy_trade'] },
}

const STATUSES = {
  HOLD: { bg: '#374151', text: '#D1D5DB' },
  ADD:  { bg: '#064E3B', text: '#6EE7B7' },
  TRIM: { bg: '#78350F', text: '#FCD34D' },
  EXIT: { bg: '#7F1D1D', text: '#FCA5A5' },
}

const ASSET_TYPE_LABELS = {
  stock: 'Stock', option: 'Option', crypto: 'Crypto',
  memecoin: 'Memecoin', copy_trade: 'Copy Trade',
}

// ─── HOOKS ────────────────────────────────────────────────────────────────────

function useLocalStorage(key, initialValue) {
  const [storedValue, setStoredValue] = useState(() => {
    try {
      const item = window.localStorage.getItem(key)
      return item ? JSON.parse(item) : initialValue
    } catch {
      return initialValue
    }
  })

  const setValue = useCallback((value) => {
    setStoredValue(prev => {
      const next = value instanceof Function ? value(prev) : value
      try { window.localStorage.setItem(key, JSON.stringify(next)) } catch {}
      return next
    })
  }, [key])

  return [storedValue, setValue]
}

// ─── HELPERS ──────────────────────────────────────────────────────────────────

function uid() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

function fmt$(v) {
  if (v == null || isNaN(v)) return '$0.00'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(v)
}

function fmtPct(v) {
  return `${Number(v || 0).toFixed(1)}%`
}

// ─── API UTILITIES ────────────────────────────────────────────────────────────

async function fetchCryptoPrices(coinIds) {
  if (!coinIds.length) return {}
  try {
    const ids = [...new Set(coinIds)].join(',')
    const res = await fetch(
      `https://api.coingecko.com/api/v3/simple/price?ids=${ids}&vs_currencies=usd`
    )
    const data = await res.json()
    const out = {}
    coinIds.forEach(id => { out[id] = data?.[id]?.usd ?? null })
    return out
  } catch {
    return {}
  }
}

async function fetchStockPrice(symbol) {
  if (!symbol) return null
  try {
    const yahooUrl = `https://query2.finance.yahoo.com/v8/finance/chart/${symbol.toUpperCase()}?interval=1d&range=1d`
    const res = await fetch(
      `https://api.allorigins.win/raw?url=${encodeURIComponent(yahooUrl)}`
    )
    const data = await res.json()
    return data?.chart?.result?.[0]?.meta?.regularMarketPrice ?? null
  } catch {
    return null
  }
}

async function callClaude(messages, systemPrompt, apiKey) {
  if (!apiKey) throw new Error('No API key. Add your Anthropic key in Settings.')
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-allow-browser': 'true',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1024,
      system: systemPrompt,
      messages: messages.map(m => ({ role: m.role, content: m.content })),
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err?.error?.message || `API error ${res.status}`)
  }
  const data = await res.json()
  return data.content[0].text
}

function buildPortfolioContext(positions, goals, totalValue) {
  const byPlatform = {}
  positions.forEach(p => {
    if (!byPlatform[p.platform]) byPlatform[p.platform] = []
    byPlatform[p.platform].push(p)
  })

  let ctx = `PORTFOLIO OVERVIEW\nTotal Value: ${fmt$(totalValue)}\n\n`

  Object.entries(byPlatform).forEach(([platform, pos]) => {
    const platTotal = pos.reduce((s, p) => s + (p.currentValue || 0), 0)
    const pct = totalValue > 0 ? ((platTotal / totalValue) * 100).toFixed(1) : '0'
    ctx += `${PLATFORMS[platform]?.name ?? platform} — ${fmt$(platTotal)} (${pct}%)\n`
    pos.forEach(p => {
      const alloc = totalValue > 0 ? ((p.currentValue / totalValue) * 100).toFixed(1) : '0'
      ctx += `  • ${p.assetName}${p.ticker ? ` [${p.ticker}]` : ''}: ${fmt$(p.currentValue)} | Alloc ${alloc}% → Target ${p.targetAllocation}% | ${p.status}\n`
      if (p.thesis)   ctx += `    Thesis: ${p.thesis}\n`
      if (p.exitPlan) ctx += `    Exit: ${p.exitPlan}\n`
      if (p.timeline) ctx += `    Timeline: ${p.timeline}\n`
    })
    ctx += '\n'
  })

  if (goals.length) {
    ctx += 'FINANCIAL GOALS:\n'
    goals.forEach(g => {
      const pct = g.targetAmount > 0 ? ((g.currentAmount / g.targetAmount) * 100).toFixed(1) : '0'
      ctx += `  • ${g.name}: ${fmt$(g.currentAmount)} / ${fmt$(g.targetAmount)} (${pct}%)${g.deadline ? ` by ${g.deadline}` : ''}\n`
    })
  }

  return ctx
}

function buildPositionContext(pos, totalValue) {
  const alloc = totalValue > 0 ? ((pos.currentValue / totalValue) * 100).toFixed(1) : '?'
  return `You are a concise investment advisor. The user is asking about one specific position. Keep answers under 200 words and be direct and actionable.

POSITION:
  Asset: ${pos.assetName}
  Ticker/ID: ${pos.ticker || 'N/A'}
  Platform: ${PLATFORMS[pos.platform]?.name ?? pos.platform}
  Type: ${ASSET_TYPE_LABELS[pos.type] ?? pos.type}
  Current Value: ${fmt$(pos.currentValue)}
  Last Known Price: ${pos.lastPrice ? fmt$(pos.lastPrice) : 'Unknown'}
  Current Allocation: ${alloc}%
  Target Allocation: ${pos.targetAllocation}%
  Status: ${pos.status}
  Thesis: ${pos.thesis || 'Not set'}
  Exit Plan: ${pos.exitPlan || 'Not set'}
  Timeline: ${pos.timeline || 'Not set'}`
}

// ─── PRICE REFRESH ─────────────────────────────────────────────────────────────

async function refreshAllPrices(positions, setPositions) {
  const cryptoPos = positions.filter(p =>
    (p.type === 'crypto' || p.type === 'memecoin') && p.ticker
  )
  if (cryptoPos.length > 0) {
    const ids = cryptoPos.map(p => p.ticker)
    const prices = await fetchCryptoPrices(ids)
    setPositions(prev =>
      prev.map(p =>
        (p.type === 'crypto' || p.type === 'memecoin') && p.ticker && prices[p.ticker] != null
          ? { ...p, lastPrice: prices[p.ticker] }
          : p
      )
    )
  }

  const stockPos = positions.filter(p => p.type === 'stock' && p.ticker)
  for (const pos of stockPos) {
    await new Promise(r => setTimeout(r, 400))
    const price = await fetchStockPrice(pos.ticker)
    if (price != null) {
      setPositions(prev =>
        prev.map(p => p.id === pos.id ? { ...p, lastPrice: price } : p)
      )
    }
  }
}

// ─── UI PRIMITIVES ────────────────────────────────────────────────────────────

function PlatformBadge({ platform }) {
  const p = PLATFORMS[platform]
  if (!p) return null
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold"
      style={{ backgroundColor: p.color + '25', color: p.color, border: `1px solid ${p.color}50` }}
    >
      {p.name}
    </span>
  )
}

function StatusTag({ status, onClick }) {
  const s = STATUSES[status] ?? STATUSES.HOLD
  return (
    <span
      onClick={onClick}
      className={`inline-flex items-center px-2.5 py-0.5 rounded text-xs font-bold tracking-wider ${onClick ? 'cursor-pointer' : ''}`}
      style={{ backgroundColor: s.bg, color: s.text }}
    >
      {status || 'HOLD'}
    </span>
  )
}

function AllocationBar({ current, target }) {
  const cur = Math.min(Math.max(Number(current) || 0, 0), 100)
  const tgt = Math.min(Math.max(Number(target) || 0, 0), 100)
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-xs text-gray-500">
        <span>Current <span className="text-gray-300">{fmtPct(cur)}</span></span>
        <span>Target <span className="text-cyan-400">{fmtPct(tgt)}</span></span>
      </div>
      <div className="space-y-1">
        <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${cur}%`, backgroundColor: '#6366F1' }}
          />
        </div>
        <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${tgt}%`, backgroundColor: '#22D3EE', opacity: 0.5 }}
          />
        </div>
      </div>
    </div>
  )
}

function ProgressBar({ current, target, color = '#6366F1' }) {
  const pct = target > 0 ? Math.min((current / target) * 100, 100) : 0
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-gray-500">
        <span className="text-white font-medium">{fmt$(current)}</span>
        <span>{fmtPct(pct)}</span>
      </div>
      <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <div className="text-right text-xs text-gray-600">of {fmt$(target)}</div>
    </div>
  )
}

function LoadingDots() {
  return (
    <div className="flex gap-1 items-center px-4 py-3">
      {[0, 1, 2].map(i => (
        <div
          key={i}
          className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce"
          style={{ animationDelay: `${i * 0.18}s` }}
        />
      ))}
    </div>
  )
}

function ChatBubble({ msg }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-3`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
          isUser
            ? 'bg-indigo-600 text-white rounded-br-sm'
            : 'bg-gray-800 text-gray-100 rounded-bl-sm'
        }`}
      >
        {msg.content}
      </div>
    </div>
  )
}

function Modal({ isOpen, onClose, title, children, fullScreen }) {
  useEffect(() => {
    if (isOpen) document.body.style.overflow = 'hidden'
    else document.body.style.overflow = ''
    return () => { document.body.style.overflow = '' }
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div
        className={`relative w-full bg-[#0D1526] flex flex-col shadow-2xl ${
          fullScreen
            ? 'h-full'
            : 'max-h-[90vh] sm:max-w-lg sm:rounded-2xl rounded-t-2xl'
        }`}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800 flex-shrink-0">
          <h2 className="text-sm font-semibold text-white truncate pr-4">{title}</h2>
          <button
            onClick={onClose}
            className="w-8 h-8 flex-shrink-0 flex items-center justify-center rounded-full bg-gray-800 text-gray-400 hover:text-white transition-colors text-sm"
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto min-h-0">{children}</div>
      </div>
    </div>
  )
}

function ChatInterface({ history, onSend, isLoading, placeholder, apiKeyMissing }) {
  const [input, setInput] = useState('')
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, isLoading])

  const send = () => {
    if (!input.trim() || isLoading) return
    onSend(input.trim())
    setInput('')
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 min-h-0">
        {history.length === 0 && (
          <div className="text-center text-gray-600 mt-10">
            <div className="text-4xl mb-3">🤖</div>
            <p className="text-sm">{placeholder || 'Start a conversation…'}</p>
          </div>
        )}
        {history.map((msg, i) => <ChatBubble key={i} msg={msg} />)}
        {isLoading && (
          <div className="flex justify-start mb-3">
            <div className="bg-gray-800 rounded-2xl rounded-bl-sm">
              <LoadingDots />
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {apiKeyMissing && (
        <div className="mx-3 mb-2 p-2.5 bg-amber-900/30 border border-amber-800/50 rounded-xl text-xs text-amber-300">
          ⚠️ Add your Anthropic API key in Settings to enable AI.
        </div>
      )}

      <div
        className="flex gap-2 p-3 border-t border-gray-800 flex-shrink-0"
        style={{ paddingBottom: 'max(12px, env(safe-area-inset-bottom))' }}
      >
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Ask anything…"
          rows={1}
          className="flex-1 bg-gray-800 text-white rounded-xl px-3 py-2.5 resize-none outline-none border border-gray-700 focus:border-indigo-500 placeholder-gray-600 leading-tight"
          style={{ minHeight: '40px', maxHeight: '100px' }}
        />
        <button
          onClick={send}
          disabled={!input.trim() || isLoading}
          className="w-10 h-10 flex-shrink-0 flex items-center justify-center rounded-xl bg-indigo-600 text-white font-bold text-lg disabled:opacity-30 hover:bg-indigo-500 transition-colors"
        >
          ↑
        </button>
      </div>
    </div>
  )
}

// ─── EDITABLE FIELD ───────────────────────────────────────────────────────────

function EditableField({ label, value, onChange, multiline, placeholder }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value || '')
  const ref = useRef(null)

  useEffect(() => { if (editing) ref.current?.focus() }, [editing])

  const save = () => { onChange(draft); setEditing(false) }
  const cancel = () => { setDraft(value || ''); setEditing(false) }

  if (editing) {
    return (
      <div className="space-y-1.5">
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest">{label}</label>
        {multiline ? (
          <textarea
            ref={ref} value={draft}
            onChange={e => setDraft(e.target.value)}
            rows={3}
            className="w-full bg-gray-800 text-white rounded-lg px-3 py-2 border border-indigo-500 outline-none resize-none leading-relaxed"
          />
        ) : (
          <input
            ref={ref} value={draft}
            onChange={e => setDraft(e.target.value)}
            className="w-full bg-gray-800 text-white rounded-lg px-3 py-2 border border-indigo-500 outline-none"
          />
        )}
        <div className="flex gap-3">
          <button onClick={save} className="text-xs text-indigo-400 font-semibold">Save</button>
          <button onClick={cancel} className="text-xs text-gray-600">Cancel</button>
        </div>
      </div>
    )
  }

  return (
    <button className="w-full text-left space-y-0.5 group" onClick={() => setEditing(true)}>
      <span className="text-xs text-gray-500 font-semibold uppercase tracking-widest">{label}</span>
      <p className="text-sm text-gray-300 group-hover:text-white transition-colors leading-relaxed">
        {value || <span className="text-gray-600 italic">{placeholder || 'Tap to add…'}</span>}
        {value && <span className="ml-1.5 text-gray-700 text-xs opacity-0 group-hover:opacity-100"> ✏️</span>}
      </p>
    </button>
  )
}

// ─── STATUS SELECTOR ──────────────────────────────────────────────────────────

function StatusSelector({ current, onChange }) {
  return (
    <div className="flex gap-2 flex-wrap">
      {Object.entries(STATUSES).map(([key, s]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className="px-3 py-1 rounded-lg text-xs font-bold tracking-wider transition-all"
          style={{
            backgroundColor: s.bg,
            color: s.text,
            outline: current === key ? `2px solid ${s.text}` : 'none',
            outlineOffset: '2px',
          }}
        >
          {key}
        </button>
      ))}
    </div>
  )
}

// ─── POSITION CARD ────────────────────────────────────────────────────────────

function PositionCard({ position, onUpdate, onDelete, totalPortfolioValue, apiKey }) {
  const [expanded, setExpanded] = useState(false)
  const [aiOpen, setAiOpen] = useState(false)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState('')

  const currentAlloc = totalPortfolioValue > 0
    ? (position.currentValue / totalPortfolioValue) * 100
    : 0

  const handleAiSend = async (text) => {
    const next = [...(position.chatHistory || []), { role: 'user', content: text }]
    onUpdate({ ...position, chatHistory: next })
    setAiLoading(true)
    setAiError('')
    try {
      const reply = await callClaude(next, buildPositionContext(position, totalPortfolioValue), apiKey)
      onUpdate({ ...position, chatHistory: [...next, { role: 'assistant', content: reply }] })
    } catch (e) {
      setAiError(e.message)
    } finally {
      setAiLoading(false)
    }
  }

  const platform = PLATFORMS[position.platform]

  return (
    <>
      <div className="bg-gray-900 rounded-2xl border border-gray-800 overflow-hidden">
        {/* Card header — always visible, tap to expand */}
        <div
          className="flex items-start justify-between p-4 cursor-pointer select-none"
          onClick={() => setExpanded(e => !e)}
        >
          <div className="flex-1 min-w-0 mr-3">
            <div className="flex items-center gap-1.5 flex-wrap mb-1">
              <PlatformBadge platform={position.platform} />
              <StatusTag status={position.status} />
            </div>
            <h3 className="font-semibold text-white leading-tight">{position.assetName}</h3>
            <div className="flex items-center gap-2 mt-0.5 flex-wrap">
              {position.ticker && (
                <span className="text-xs text-gray-500 font-mono uppercase">{position.ticker}</span>
              )}
              {position.lastPrice != null && (
                <span className="text-xs text-gray-400">{fmt$(position.lastPrice)}</span>
              )}
              <span className="text-xs text-gray-600">{ASSET_TYPE_LABELS[position.type]}</span>
            </div>
          </div>
          <div className="text-right flex-shrink-0">
            <div className="text-base font-bold text-white">{fmt$(position.currentValue)}</div>
            <div className="text-xs text-gray-500">{fmtPct(currentAlloc)} of portfolio</div>
          </div>
        </div>

        {/* Allocation bars — always visible */}
        <div className="px-4 pb-3">
          <AllocationBar current={currentAlloc} target={position.targetAllocation} />
        </div>

        {/* Expanded details */}
        {expanded && (
          <div className="px-4 pb-4 pt-3 border-t border-gray-800 space-y-4">
            <div>
              <p className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-2">Status</p>
              <StatusSelector
                current={position.status}
                onChange={s => onUpdate({ ...position, status: s })}
              />
            </div>

            <EditableField
              label="My Thesis"
              value={position.thesis}
              onChange={v => onUpdate({ ...position, thesis: v })}
              placeholder="Why are you in this position?"
              multiline
            />

            <EditableField
              label="Exit Plan"
              value={position.exitPlan}
              onChange={v => onUpdate({ ...position, exitPlan: v })}
              placeholder="When / how will you exit?"
              multiline
            />

            <EditableField
              label="Timeline"
              value={position.timeline}
              onChange={v => onUpdate({ ...position, timeline: v })}
              placeholder="e.g. Q4 2025 · 6 months · Long-term"
            />

            {aiError && (
              <div className="p-2.5 bg-red-900/30 border border-red-800/50 rounded-xl text-xs text-red-300">
                {aiError}
              </div>
            )}

            <div className="flex gap-2 pt-1">
              <button
                onClick={() => { setAiError(''); setAiOpen(true) }}
                className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-medium transition-all"
                style={{
                  backgroundColor: platform?.color + '18',
                  color: platform?.color,
                  border: `1px solid ${platform?.color}40`,
                }}
              >
                🤖 Ask AI
              </button>
              <button
                onClick={() => onDelete(position.id)}
                className="px-3 py-2.5 rounded-xl text-red-400 hover:bg-red-900/20 border border-red-900/30 transition-colors"
              >
                🗑
              </button>
            </div>
          </div>
        )}

        <div className="flex justify-center pb-1 opacity-30">
          <span className="text-gray-500 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      <Modal
        isOpen={aiOpen}
        onClose={() => setAiOpen(false)}
        title={`AI — ${position.assetName}`}
        fullScreen
      >
        <ChatInterface
          history={position.chatHistory || []}
          onSend={handleAiSend}
          isLoading={aiLoading}
          placeholder={`Ask about your ${position.assetName} position…`}
          apiKeyMissing={!apiKey}
        />
      </Modal>
    </>
  )
}

// ─── POSITION LIST ────────────────────────────────────────────────────────────

function PositionList({ positions, onUpdate, onDelete, onRefresh, refreshing, totalPortfolioValue, apiKey }) {
  const [filter, setFilter] = useState('all')

  const tabs = [
    { key: 'all', label: 'All', color: '#6366F1' },
    ...Object.entries(PLATFORMS).map(([k, v]) => ({ key: k, label: v.name, color: v.color })),
  ]

  const filtered = filter === 'all' ? positions : positions.filter(p => p.platform === filter)
  const count = key => key === 'all' ? positions.length : positions.filter(p => p.platform === key).length

  return (
    <div className="p-4 space-y-4">
      {/* Platform filter tabs */}
      <div className="flex gap-2 overflow-x-auto scrollbar-none pb-1">
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setFilter(tab.key)}
            className="flex-shrink-0 px-3 py-1.5 rounded-full text-xs font-medium border transition-all"
            style={filter === tab.key
              ? { backgroundColor: tab.color + '30', color: tab.color, borderColor: tab.color + '70' }
              : { backgroundColor: 'transparent', color: '#6B7280', borderColor: '#374151' }
            }
          >
            {tab.label} {count(tab.key)}
          </button>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-600">
          {filtered.length} position{filtered.length !== 1 ? 's' : ''}
        </span>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-40 flex items-center gap-1 transition-colors"
        >
          <span className={refreshing ? 'animate-spin inline-block' : ''}>🔄</span>
          {refreshing ? 'Refreshing…' : 'Refresh prices'}
        </button>
      </div>

      {filtered.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          <div className="text-5xl mb-4">📭</div>
          <p className="text-sm">No positions yet</p>
          <p className="text-xs mt-1 text-gray-700">Add one from the + tab</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map(pos => (
            <PositionCard
              key={pos.id}
              position={pos}
              onUpdate={onUpdate}
              onDelete={onDelete}
              totalPortfolioValue={totalPortfolioValue}
              apiKey={apiKey}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── PORTFOLIO OVERVIEW ───────────────────────────────────────────────────────

function PortfolioOverview({ positions, goals, settings, onUpdateSettings, onRefresh, refreshing }) {
  const [editingValue, setEditingValue] = useState(false)
  const [valueDraft, setValueDraft] = useState('')

  const total = settings.totalPortfolioValue || 0

  const platformStats = Object.entries(PLATFORMS).map(([key, p]) => {
    const pos = positions.filter(x => x.platform === key)
    const val = pos.reduce((s, x) => s + (x.currentValue || 0), 0)
    const pct = total > 0 ? (val / total) * 100 : 0
    return { key, ...p, val, pct, count: pos.length }
  })

  const tracked = positions.reduce((s, p) => s + (p.currentValue || 0), 0)

  return (
    <div className="p-4 space-y-4">
      {/* Hero value card */}
      <div className="bg-gray-900 rounded-2xl p-5 border border-gray-800 text-center">
        <p className="text-xs text-gray-500 uppercase tracking-widest mb-3">Total Portfolio Value</p>

        {editingValue ? (
          <div className="flex items-center justify-center gap-2">
            <span className="text-3xl text-gray-500 font-light">$</span>
            <input
              type="number"
              defaultValue={total}
              onFocus={e => e.target.select()}
              onChange={e => setValueDraft(e.target.value)}
              autoFocus
              className="bg-transparent text-4xl font-bold text-white outline-none w-44 text-center border-b-2 border-indigo-500"
            />
            <button
              onClick={() => {
                onUpdateSettings({ ...settings, totalPortfolioValue: parseFloat(valueDraft) || total })
                setEditingValue(false)
              }}
              className="text-indigo-400 text-sm font-semibold ml-1"
            >
              Save
            </button>
          </div>
        ) : (
          <button
            onClick={() => { setValueDraft(String(total)); setEditingValue(true) }}
            className="group"
          >
            <div className="text-4xl font-bold text-white group-hover:text-indigo-300 transition-colors">
              {total === 0 ? <span className="text-gray-600">$0 · Tap to set</span> : fmt$(total)}
            </div>
          </button>
        )}

        {total > 0 && (
          <div className="mt-3 flex justify-center gap-4 text-xs text-gray-600">
            <span>{fmt$(tracked)} tracked</span>
            <span>·</span>
            <span>{fmt$(Math.max(0, total - tracked))} untracked</span>
          </div>
        )}

        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="mt-3 flex items-center gap-1.5 mx-auto text-xs text-gray-600 hover:text-gray-400 transition-colors disabled:opacity-40"
        >
          <span className={refreshing ? 'animate-spin inline-block' : ''}>🔄</span>
          {refreshing ? 'Refreshing prices…' : 'Refresh all prices'}
        </button>
      </div>

      {/* Stacked allocation bar */}
      {total > 0 && platformStats.some(p => p.pct > 0) && (
        <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
          <p className="text-xs text-gray-500 uppercase tracking-widest mb-3">Allocation</p>
          <div className="h-3 bg-gray-800 rounded-full overflow-hidden flex">
            {platformStats.filter(p => p.pct > 0.1).map(p => (
              <div
                key={p.key}
                className="h-full first:rounded-l-full last:rounded-r-full"
                style={{ width: `${p.pct}%`, backgroundColor: p.color }}
                title={`${p.name}: ${fmtPct(p.pct)}`}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-3 mt-3">
            {platformStats.filter(p => p.pct > 0.1).map(p => (
              <div key={p.key} className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: p.color }} />
                <span className="text-xs text-gray-400">{p.name} {fmtPct(p.pct)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Platform breakdown cards */}
      <div className="space-y-2">
        <p className="text-xs text-gray-500 uppercase tracking-widest px-1">By Platform</p>
        {platformStats.map(p => (
          <div key={p.key} className="bg-gray-900 rounded-xl px-4 py-3 border border-gray-800">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: p.color }} />
                <span className="text-sm font-medium text-white">{p.name}</span>
                <span className="text-xs text-gray-600">{p.count} pos.</span>
              </div>
              <div className="text-right">
                <span className="text-sm font-semibold text-white">{fmt$(p.val)}</span>
                <span className="text-xs text-gray-500 ml-2">{fmtPct(p.pct)}</span>
              </div>
            </div>
            <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{ width: `${p.pct}%`, backgroundColor: p.color }}
              />
            </div>
          </div>
        ))}
      </div>

      {positions.length === 0 && (
        <div className="text-center py-10 text-gray-700">
          <div className="text-4xl mb-3">📈</div>
          <p className="text-sm">Add your first position from the + tab</p>
        </div>
      )}
    </div>
  )
}

// ─── GOALS BOARD ──────────────────────────────────────────────────────────────

function GoalCard({ goal, onUpdate, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ ...goal })

  const pct = goal.targetAmount > 0 ? (goal.currentAmount / goal.targetAmount) * 100 : 0
  const barColor = pct >= 100 ? '#10B981' : pct >= 60 ? '#6366F1' : '#F59E0B'

  if (editing) {
    return (
      <div className="bg-gray-900 rounded-2xl p-4 border border-indigo-800/60 space-y-3">
        <input
          value={draft.name}
          onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
          placeholder="Goal name"
          autoFocus
          className="w-full bg-gray-800 text-white rounded-xl px-3 py-2.5 border border-gray-700 outline-none"
        />
        <div className="grid grid-cols-2 gap-2">
          {[['targetAmount', 'Target ($)'], ['currentAmount', 'Current ($)']].map(([field, label]) => (
            <div key={field}>
              <label className="text-xs text-gray-500 mb-1 block">{label}</label>
              <input
                type="number"
                value={draft[field]}
                onChange={e => setDraft(d => ({ ...d, [field]: parseFloat(e.target.value) || 0 }))}
                className="w-full bg-gray-800 text-white rounded-xl px-3 py-2.5 border border-gray-700 outline-none"
              />
            </div>
          ))}
        </div>
        <div>
          <label className="text-xs text-gray-500 mb-1 block">Deadline</label>
          <input
            type="date"
            value={draft.deadline}
            onChange={e => setDraft(d => ({ ...d, deadline: e.target.value }))}
            className="w-full bg-gray-800 text-white rounded-xl px-3 py-2.5 border border-gray-700 outline-none"
          />
        </div>
        <textarea
          value={draft.notes}
          onChange={e => setDraft(d => ({ ...d, notes: e.target.value }))}
          placeholder="Notes (optional)"
          rows={2}
          className="w-full bg-gray-800 text-white rounded-xl px-3 py-2.5 border border-gray-700 outline-none resize-none"
        />
        <div className="flex gap-2">
          <button onClick={() => { onUpdate(draft); setEditing(false) }} className="flex-1 py-2.5 rounded-xl bg-indigo-600 text-white text-sm font-semibold">
            Save
          </button>
          <button onClick={() => { setDraft({ ...goal }); setEditing(false) }} className="px-4 py-2.5 rounded-xl bg-gray-800 text-gray-400 text-sm">
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="bg-gray-900 rounded-2xl p-4 border border-gray-800">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="font-semibold text-white">{goal.name}</h3>
          {goal.deadline && (
            <p className="text-xs text-gray-500 mt-0.5">
              by {new Date(goal.deadline + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
            </p>
          )}
        </div>
        <div className="flex gap-1 ml-2">
          <button onClick={() => setEditing(true)} className="w-8 h-8 flex items-center justify-center rounded-lg bg-gray-800 text-gray-400 hover:text-white text-xs">✏️</button>
          <button onClick={() => onDelete(goal.id)} className="w-8 h-8 flex items-center justify-center rounded-lg bg-gray-800 text-red-400 hover:text-red-300 text-xs">🗑</button>
        </div>
      </div>
      <ProgressBar current={goal.currentAmount} target={goal.targetAmount} color={barColor} />
      {goal.notes && <p className="text-xs text-gray-500 mt-3 leading-relaxed">{goal.notes}</p>}
    </div>
  )
}

function GoalsBoard({ goals, onAdd, onUpdate, onDelete }) {
  const [showAdd, setShowAdd] = useState(false)
  const [draft, setDraft] = useState({ name: '', targetAmount: '', currentAmount: '', deadline: '', notes: '' })

  const handleAdd = () => {
    if (!draft.name.trim()) return
    onAdd({
      id: uid(),
      name: draft.name.trim(),
      targetAmount: parseFloat(draft.targetAmount) || 0,
      currentAmount: parseFloat(draft.currentAmount) || 0,
      deadline: draft.deadline,
      notes: draft.notes,
    })
    setDraft({ name: '', targetAmount: '', currentAmount: '', deadline: '', notes: '' })
    setShowAdd(false)
  }

  const totalTarget  = goals.reduce((s, g) => s + g.targetAmount, 0)
  const totalCurrent = goals.reduce((s, g) => s + g.currentAmount, 0)

  return (
    <div className="p-4 space-y-4">
      {goals.length > 1 && (
        <div className="bg-gray-900 rounded-2xl p-4 border border-gray-800">
          <p className="text-xs text-gray-500 uppercase tracking-widest mb-2">Overall Progress</p>
          <ProgressBar current={totalCurrent} target={totalTarget} color="#6366F1" />
        </div>
      )}

      {goals.length === 0 && !showAdd && (
        <div className="text-center py-20 text-gray-600">
          <div className="text-5xl mb-4">🎯</div>
          <p className="text-sm">No goals yet</p>
          <p className="text-xs mt-1 text-gray-700">Set a financial milestone below</p>
        </div>
      )}

      {goals.map(g => (
        <GoalCard key={g.id} goal={g} onUpdate={onUpdate} onDelete={onDelete} />
      ))}

      {showAdd ? (
        <div className="bg-gray-900 rounded-2xl p-4 border border-indigo-800/60 space-y-3">
          <p className="text-sm font-semibold text-white">New Goal</p>
          <input
            value={draft.name}
            onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
            placeholder="e.g. Emergency Fund"
            autoFocus
            className="w-full bg-gray-800 text-white rounded-xl px-3 py-2.5 border border-gray-700 outline-none"
          />
          <div className="grid grid-cols-2 gap-2">
            {[['targetAmount', 'Target ($)'], ['currentAmount', 'Current ($)']].map(([field, label]) => (
              <div key={field}>
                <label className="text-xs text-gray-500 mb-1 block">{label}</label>
                <input
                  type="number"
                  value={draft[field]}
                  onChange={e => setDraft(d => ({ ...d, [field]: e.target.value }))}
                  className="w-full bg-gray-800 text-white rounded-xl px-3 py-2.5 border border-gray-700 outline-none"
                />
              </div>
            ))}
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Deadline</label>
            <input type="date" value={draft.deadline} onChange={e => setDraft(d => ({ ...d, deadline: e.target.value }))} className="w-full bg-gray-800 text-white rounded-xl px-3 py-2.5 border border-gray-700 outline-none" />
          </div>
          <textarea value={draft.notes} onChange={e => setDraft(d => ({ ...d, notes: e.target.value }))} placeholder="Notes (optional)" rows={2} className="w-full bg-gray-800 text-white rounded-xl px-3 py-2.5 border border-gray-700 outline-none resize-none" />
          <div className="flex gap-2">
            <button onClick={handleAdd} disabled={!draft.name.trim()} className="flex-1 py-2.5 rounded-xl bg-indigo-600 text-white text-sm font-semibold disabled:opacity-40">Add Goal</button>
            <button onClick={() => setShowAdd(false)} className="px-4 py-2.5 rounded-xl bg-gray-800 text-gray-400 text-sm">Cancel</button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setShowAdd(true)}
          className="w-full py-3.5 rounded-xl border border-dashed border-gray-700 text-gray-500 hover:border-indigo-600 hover:text-indigo-400 text-sm transition-colors flex items-center justify-center gap-2"
        >
          <span className="text-lg leading-none">＋</span> Add Goal
        </button>
      )}
    </div>
  )
}

// ─── ADD POSITION FORM ────────────────────────────────────────────────────────

function AddPositionForm({ onAdd, totalPortfolioValue }) {
  const blank = {
    platform: 'robinhood', type: 'stock', assetName: '', ticker: '',
    currentValue: '', targetAllocation: '', thesis: '', exitPlan: '',
    timeline: '', status: 'HOLD',
  }
  const [form, setForm] = useState(blank)
  const [done, setDone] = useState(false)

  const set = (patch) => setForm(f => ({ ...f, ...patch }))

  const typeOptions = PLATFORMS[form.platform]?.types ?? []

  const handlePlatform = (platform) => {
    const types = PLATFORMS[platform]?.types ?? []
    set({ platform, type: types[0] ?? 'stock' })
  }

  const autoAlloc = totalPortfolioValue > 0 && form.currentValue
    ? ((parseFloat(form.currentValue) || 0) / totalPortfolioValue * 100).toFixed(1)
    : null

  const submit = () => {
    if (!form.assetName.trim()) return
    onAdd({
      ...form,
      id: uid(),
      currentValue: parseFloat(form.currentValue) || 0,
      targetAllocation: parseFloat(form.targetAllocation) || 0,
      chatHistory: [],
      lastPrice: null,
      createdAt: new Date().toISOString(),
    })
    setForm(blank)
    setDone(true)
    setTimeout(() => setDone(false), 2000)
  }

  if (done) {
    return (
      <div className="p-8 text-center">
        <div className="text-5xl mb-4">✅</div>
        <h3 className="text-lg font-semibold text-white">Position Added!</h3>
        <p className="text-gray-500 text-sm mt-1">View it in the Positions tab</p>
      </div>
    )
  }

  return (
    <div className="p-4 space-y-5">
      <h2 className="text-lg font-semibold text-white">Add Position</h2>

      {/* Platform */}
      <div>
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-2 block">Platform</label>
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(PLATFORMS).map(([key, p]) => (
            <button
              key={key}
              onClick={() => handlePlatform(key)}
              className="py-2.5 px-3 rounded-xl text-sm font-medium border transition-all flex items-center gap-2"
              style={form.platform === key
                ? { backgroundColor: p.color + '22', color: p.color, borderColor: p.color + '60' }
                : { backgroundColor: 'transparent', color: '#6B7280', borderColor: '#374151' }
              }
            >
              <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: p.color }} />
              {p.name}
            </button>
          ))}
        </div>
      </div>

      {/* Asset type */}
      <div>
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-2 block">Asset Type</label>
        <div className="flex gap-2 flex-wrap">
          {typeOptions.map(t => (
            <button
              key={t}
              onClick={() => set({ type: t })}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                form.type === t
                  ? 'bg-indigo-600/20 text-indigo-300 border-indigo-600/50'
                  : 'text-gray-500 border-gray-700'
              }`}
            >
              {ASSET_TYPE_LABELS[t]}
            </button>
          ))}
        </div>
      </div>

      {/* Asset name */}
      <div>
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-1 block">Asset Name <span className="text-indigo-400">*</span></label>
        <input
          value={form.assetName}
          onChange={e => set({ assetName: e.target.value })}
          placeholder={form.type === 'stock' ? 'Apple Inc.' : form.type === 'crypto' ? 'Bitcoin' : 'BONK'}
          className="w-full bg-gray-800 text-white rounded-xl px-4 py-3 border border-gray-700 outline-none focus:border-indigo-500 transition-colors"
        />
      </div>

      {/* Ticker */}
      <div>
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-1 block">
          Ticker / CoinGecko ID
          <span className="ml-1 text-gray-600 font-normal normal-case">(for live prices)</span>
        </label>
        <input
          value={form.ticker}
          onChange={e => set({ ticker: e.target.value })}
          placeholder={form.type === 'stock' ? 'AAPL' : form.type === 'copy_trade' ? 'optional' : 'bitcoin · solana · bonk'}
          className="w-full bg-gray-800 text-white font-mono rounded-xl px-4 py-3 border border-gray-700 outline-none focus:border-indigo-500 transition-colors"
        />
        {(form.type === 'crypto' || form.type === 'memecoin') && (
          <p className="text-xs text-gray-600 mt-1">
            Use the CoinGecko ID — find it at coingecko.com (e.g. "bonk", "dogwifhat")
          </p>
        )}
      </div>

      {/* Value & allocation */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-1 block">Value ($)</label>
          <input
            type="number"
            value={form.currentValue}
            onChange={e => set({ currentValue: e.target.value })}
            placeholder="0"
            className="w-full bg-gray-800 text-white rounded-xl px-3 py-3 border border-gray-700 outline-none focus:border-indigo-500"
          />
          {autoAlloc && (
            <p className="text-xs text-gray-600 mt-1">≈ {autoAlloc}% of portfolio</p>
          )}
        </div>
        <div>
          <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-1 block">Target %</label>
          <input
            type="number"
            value={form.targetAllocation}
            onChange={e => set({ targetAllocation: e.target.value })}
            placeholder="0"
            className="w-full bg-gray-800 text-white rounded-xl px-3 py-3 border border-gray-700 outline-none focus:border-indigo-500"
          />
        </div>
      </div>

      {/* Status */}
      <div>
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-2 block">Status</label>
        <StatusSelector current={form.status} onChange={s => set({ status: s })} />
      </div>

      {/* Thesis */}
      <div>
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-1 block">My Thesis</label>
        <textarea
          value={form.thesis}
          onChange={e => set({ thesis: e.target.value })}
          placeholder="Why do you hold this position?"
          rows={2}
          className="w-full bg-gray-800 text-white rounded-xl px-4 py-3 border border-gray-700 outline-none focus:border-indigo-500 resize-none"
        />
      </div>

      {/* Exit plan */}
      <div>
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-1 block">Exit Plan</label>
        <textarea
          value={form.exitPlan}
          onChange={e => set({ exitPlan: e.target.value })}
          placeholder="When / how will you exit?"
          rows={2}
          className="w-full bg-gray-800 text-white rounded-xl px-4 py-3 border border-gray-700 outline-none focus:border-indigo-500 resize-none"
        />
      </div>

      {/* Timeline */}
      <div>
        <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-1 block">Timeline</label>
        <input
          value={form.timeline}
          onChange={e => set({ timeline: e.target.value })}
          placeholder="e.g. Q4 2025 · 6 months · Long-term hold"
          className="w-full bg-gray-800 text-white rounded-xl px-4 py-3 border border-gray-700 outline-none focus:border-indigo-500"
        />
      </div>

      <button
        onClick={submit}
        disabled={!form.assetName.trim()}
        className="w-full py-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        Add Position
      </button>
    </div>
  )
}

// ─── SETTINGS PANEL ───────────────────────────────────────────────────────────

function SettingsPanel({ settings, onUpdateSettings, positions, goals }) {
  const [showKey, setShowKey] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)

  return (
    <div className="p-4 space-y-4">
      <h2 className="text-lg font-semibold text-white">Settings</h2>

      {/* AI key */}
      <div className="bg-gray-900 rounded-2xl p-4 border border-gray-800 space-y-3">
        <h3 className="text-sm font-semibold text-white">AI Co-Pilot</h3>

        <div>
          <label className="text-xs text-gray-500 font-semibold uppercase tracking-widest mb-1 block">
            Anthropic API Key
          </label>
          <div className="flex gap-2">
            <input
              type={showKey ? 'text' : 'password'}
              value={settings.anthropicApiKey || ''}
              onChange={e => onUpdateSettings({ ...settings, anthropicApiKey: e.target.value })}
              placeholder="sk-ant-api03-…"
              className="flex-1 bg-gray-800 text-white font-mono rounded-xl px-3 py-2.5 border border-gray-700 outline-none focus:border-indigo-500"
            />
            <button
              onClick={() => setShowKey(v => !v)}
              className="px-3 py-2.5 rounded-xl bg-gray-800 text-gray-400 border border-gray-700 text-sm"
            >
              {showKey ? '🙈' : '👁️'}
            </button>
          </div>
          <p className="text-xs text-gray-600 mt-1.5 leading-relaxed">
            Get your key at console.anthropic.com → API Keys. It's stored only in your browser — never shared.
          </p>
        </div>

        <div className="flex items-center gap-2 p-3 rounded-xl bg-gray-800/50 border border-gray-700/50">
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${settings.anthropicApiKey ? 'bg-emerald-500' : 'bg-gray-600'}`} />
          <span className="text-xs text-gray-400">
            {settings.anthropicApiKey
              ? 'API key configured — AI features are active'
              : 'No API key — AI co-pilot is disabled'}
          </span>
        </div>
      </div>

      {/* Stats */}
      <div className="bg-gray-900 rounded-2xl p-4 border border-gray-800">
        <h3 className="text-sm font-semibold text-white mb-3">Portfolio Stats</h3>
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: 'Positions', value: positions.length },
            { label: 'Goals', value: goals.length },
            { label: 'Platforms', value: new Set(positions.map(p => p.platform)).size },
            { label: 'AI Messages', value: positions.reduce((s, p) => s + (p.chatHistory?.length || 0), 0) + (settings.globalChatHistory?.length || 0) },
          ].map(stat => (
            <div key={stat.label} className="bg-gray-800 rounded-xl p-3">
              <div className="text-2xl font-bold text-white">{stat.value}</div>
              <div className="text-xs text-gray-500 mt-0.5">{stat.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* How to use */}
      <div className="bg-gray-900 rounded-2xl p-4 border border-gray-800 space-y-2">
        <h3 className="text-sm font-semibold text-white">Price Lookups</h3>
        <div className="space-y-1 text-xs text-gray-500 leading-relaxed">
          <p>• <span className="text-gray-300">Stocks (Robinhood)</span> — enter the ticker symbol (AAPL, TSLA, NVDA)</p>
          <p>• <span className="text-gray-300">Crypto (Kraken / Phantom)</span> — use the CoinGecko ID (bitcoin, ethereum, solana, bonk)</p>
          <p>• <span className="text-gray-300">Options / Copy trades</span> — update value manually</p>
          <p>• Prices refresh automatically on load and via the 🔄 button</p>
        </div>
      </div>

      {/* Danger zone */}
      <div className="bg-gray-900 rounded-2xl p-4 border border-red-900/30">
        <h3 className="text-sm font-semibold text-red-400 mb-3">Danger Zone</h3>
        {confirmClear ? (
          <div className="space-y-3">
            <p className="text-xs text-gray-400">This deletes all positions, goals, and settings. Cannot be undone.</p>
            <div className="flex gap-2">
              <button
                onClick={() => { localStorage.clear(); window.location.reload() }}
                className="flex-1 py-2.5 rounded-xl bg-red-600 text-white text-sm font-semibold"
              >
                Yes, delete everything
              </button>
              <button onClick={() => setConfirmClear(false)} className="px-4 py-2.5 rounded-xl bg-gray-800 text-gray-400 text-sm">
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setConfirmClear(true)}
            className="w-full py-2.5 rounded-xl border border-red-900/50 text-red-400 text-sm hover:bg-red-900/20 transition-colors"
          >
            Clear All Data
          </button>
        )}
      </div>

      <div className="text-center text-xs text-gray-700 pb-2">
        Portfolio Intelligence v1.0 · Data stored locally on your device
      </div>
    </div>
  )
}

// ─── GLOBAL AI CO-PILOT ───────────────────────────────────────────────────────

function GlobalAICopilot({ positions, goals, settings, onUpdateSettings }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const history = settings.globalChatHistory || []

  const handleSend = async (text) => {
    const next = [...history, { role: 'user', content: text }]
    onUpdateSettings({ ...settings, globalChatHistory: next })
    setLoading(true)
    setError('')
    try {
      const ctx = buildPortfolioContext(positions, goals, settings.totalPortfolioValue || 0)
      const system = `You are an intelligent personal portfolio advisor with full visibility into the user's investments. Be concise, analytical, and give specific actionable advice. Reference specific positions by name when relevant.

${ctx}`
      const reply = await callClaude(next, system, settings.anthropicApiKey)
      onUpdateSettings({ ...settings, globalChatHistory: [...next, { role: 'assistant', content: reply }] })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      {/* Floating button */}
      <button
        onClick={() => { setError(''); setOpen(true) }}
        className="fixed z-40 right-4 flex items-center justify-center text-2xl rounded-full shadow-2xl transition-transform hover:scale-105 active:scale-95"
        style={{
          bottom: 'calc(80px + env(safe-area-inset-bottom))',
          width: '56px',
          height: '56px',
          background: 'linear-gradient(135deg, #6366F1, #8B5CF6)',
          boxShadow: '0 0 24px rgba(99,102,241,0.5)',
        }}
        aria-label="Open AI Co-Pilot"
      >
        🤖
      </button>

      <Modal
        isOpen={open}
        onClose={() => setOpen(false)}
        title="AI Portfolio Co-Pilot"
        fullScreen
      >
        {error && (
          <div className="mx-3 mt-3 p-3 bg-red-900/30 border border-red-800/50 rounded-xl text-xs text-red-300">
            {error}
          </div>
        )}
        <ChatInterface
          history={history}
          onSend={handleSend}
          isLoading={loading}
          placeholder="Ask about your full portfolio, risk, rebalancing, market outlook…"
          apiKeyMissing={!settings.anthropicApiKey}
        />
      </Modal>
    </>
  )
}

// ─── BOTTOM NAVIGATION ────────────────────────────────────────────────────────

const NAV = [
  { id: 'overview',   label: 'Overview',   icon: '📊' },
  { id: 'positions',  label: 'Positions',  icon: '💼' },
  { id: 'goals',      label: 'Goals',      icon: '🎯' },
  { id: 'add',        label: 'Add',        icon: '＋' },
  { id: 'settings',   label: 'Settings',   icon: '⚙️' },
]

function BottomNav({ active, onChange }) {
  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-30 border-t border-gray-800 flex"
      style={{
        backgroundColor: 'rgba(10,15,30,0.97)',
        backdropFilter: 'blur(12px)',
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      {NAV.map(tab => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={`flex-1 flex flex-col items-center gap-0.5 py-2.5 transition-all ${
            active === tab.id ? 'text-indigo-400' : 'text-gray-700 hover:text-gray-500'
          }`}
        >
          <span className="text-lg leading-none">{tab.icon}</span>
          <span className="text-[10px] font-medium">{tab.label}</span>
        </button>
      ))}
    </nav>
  )
}

// ─── ROOT APP ─────────────────────────────────────────────────────────────────

export default function App() {
  const [positions, setPositions]   = useLocalStorage('pf_positions', [])
  const [goals, setGoals]           = useLocalStorage('pf_goals', [])
  const [settings, setSettings]     = useLocalStorage('pf_settings', {
    totalPortfolioValue: 0,
    anthropicApiKey: '',
    globalChatHistory: [],
  })
  const [activeTab, setActiveTab]   = useState('overview')
  const [refreshing, setRefreshing] = useState(false)

  const handleRefresh = useCallback(async () => {
    if (refreshing) return
    setRefreshing(true)
    try {
      await refreshAllPrices(positions, setPositions)
    } finally {
      setRefreshing(false)
    }
  }, [positions, setPositions, refreshing])

  // Auto-refresh prices on first load
  useEffect(() => {
    const saved = JSON.parse(localStorage.getItem('pf_positions') || '[]')
    if (saved.length > 0) refreshAllPrices(saved, setPositions)
  }, []) // intentional empty deps — one-time mount refresh

  const handleUpdatePosition = useCallback((updated) => {
    setPositions(prev => prev.map(p => p.id === updated.id ? updated : p))
  }, [setPositions])

  const handleDeletePosition = useCallback((id) => {
    setPositions(prev => prev.filter(p => p.id !== id))
  }, [setPositions])

  const handleAddPosition = useCallback((pos) => {
    setPositions(prev => [pos, ...prev])
    setActiveTab('positions')
  }, [setPositions])

  const handleAddGoal    = useCallback((g) => setGoals(prev => [...prev, g]), [setGoals])
  const handleUpdateGoal = useCallback((g) => setGoals(prev => prev.map(x => x.id === g.id ? g : x)), [setGoals])
  const handleDeleteGoal = useCallback((id) => setGoals(prev => prev.filter(g => g.id !== id)), [setGoals])

  const total = settings.totalPortfolioValue || 0

  return (
    <div className="min-h-screen bg-[#0A0F1E] text-white">
      <div className="max-w-lg mx-auto min-h-screen" style={{ paddingBottom: 'calc(80px + env(safe-area-inset-bottom))' }}>

        {/* Page header */}
        <div className="sticky top-0 z-20 px-4 py-3 flex items-center justify-between"
          style={{ backgroundColor: 'rgba(10,15,30,0.95)', backdropFilter: 'blur(12px)', borderBottom: '1px solid rgba(31,41,55,0.6)' }}
        >
          <div className="flex items-center gap-2">
            <span className="text-lg">📊</span>
            <span className="font-semibold text-white text-sm">Portfolio Intelligence</span>
          </div>
          {total > 0 && (
            <span className="text-sm font-bold text-indigo-300">{fmt$(total)}</span>
          )}
        </div>

        {/* Tab content */}
        {activeTab === 'overview' && (
          <PortfolioOverview
            positions={positions}
            goals={goals}
            settings={settings}
            onUpdateSettings={setSettings}
            onRefresh={handleRefresh}
            refreshing={refreshing}
          />
        )}
        {activeTab === 'positions' && (
          <PositionList
            positions={positions}
            onUpdate={handleUpdatePosition}
            onDelete={handleDeletePosition}
            onRefresh={handleRefresh}
            refreshing={refreshing}
            totalPortfolioValue={total}
            apiKey={settings.anthropicApiKey}
          />
        )}
        {activeTab === 'goals' && (
          <GoalsBoard
            goals={goals}
            onAdd={handleAddGoal}
            onUpdate={handleUpdateGoal}
            onDelete={handleDeleteGoal}
          />
        )}
        {activeTab === 'add' && (
          <AddPositionForm
            onAdd={handleAddPosition}
            totalPortfolioValue={total}
          />
        )}
        {activeTab === 'settings' && (
          <SettingsPanel
            settings={settings}
            onUpdateSettings={setSettings}
            positions={positions}
            goals={goals}
          />
        )}
      </div>

      <BottomNav active={activeTab} onChange={setActiveTab} />

      <GlobalAICopilot
        positions={positions}
        goals={goals}
        settings={settings}
        onUpdateSettings={setSettings}
      />
    </div>
  )
}
