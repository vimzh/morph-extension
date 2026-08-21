import { useState } from 'react'
import { BrandLogo } from '@/components/brand-logo'
import { MessageResponse } from '@/components/ai-elements/message'
import { ModeToggle } from '@/components/mode-toggle'
import { Button } from '@/components/ui/button'
import './App.css'

const API_URL = 'http://localhost:8000'

export default function App() {
  const [query, setQuery] = useState('')
  const [response, setResponse] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return

    setLoading(true)
    setError('')
    setResponse('')

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })

      if (!res.ok) {
        throw new Error(`Server error: ${res.status}`)
      }

      const data = await res.json()
      setResponse(data.message)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="chat-container">
      <header className="popup-header">
        <div className="popup-brand">
          <BrandLogo size={30} />
          <div>
            <span>Morph</span>
            <p>Build your browser</p>
          </div>
        </div>
        <ModeToggle />
      </header>

      <form onSubmit={handleSubmit} className="chat-form">
        <div className="popup-input">
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Describe what you want to change…"
            rows={3}
            disabled={loading}
          />
          <Button type="submit" size="icon" disabled={loading || !query.trim()} aria-label="Send message">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="m5 12 14-7-4 14-3-6-7-1Z" />
            </svg>
          </Button>
        </div>
      </form>

      {error && <div className="error">{error}</div>}

      {response && (
        <div className="response">
          <MessageResponse>{response}</MessageResponse>
        </div>
      )}
    </div>
  )
}
