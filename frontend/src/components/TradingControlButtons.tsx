import { useState } from 'react';
import { CircleStop, Play } from 'lucide-react';
import { apiUrl, displayApiUrl } from '../config/api';

interface TradingControlButtonsProps {
  stopped?: boolean;
  compact?: boolean;
}

async function fetchWithTimeout(url: string, options: RequestInit = {}, timeoutMs = 5000) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    return response;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function TradingControlButtons({ stopped = false, compact = false }: TradingControlButtonsProps) {
  const [busy, setBusy] = useState(false);
  const [localStopped, setLocalStopped] = useState(stopped);
  const [message, setMessage] = useState<string | null>(null);

  const sendControl = async (action: 'stop' | 'resume') => {
    setBusy(true);
    setMessage(null);
    const reason = action === 'stop' ? 'Stopped from NexusQuant terminal UI' : 'Resumed from NexusQuant terminal UI';
    try {
      let response = await fetchWithTimeout(`${apiUrl}/api/execution/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      });

      if (!response.ok) {
        response = await fetchWithTimeout(`${apiUrl}/api/execution/${action}-now`);
      }

      if (!response.ok) {
        throw new Error(`Backend returned ${response.status}`);
      }

      const payload = await response.json();
      const stoppedNow = Boolean(payload.autoTradingStopped);
      setLocalStopped(stoppedNow);
      setMessage(stoppedNow ? 'AUTO TRADING STOPPED' : 'AUTO TRADING RESUMED');
    } catch (error) {
      setMessage(`Control failed: ${error instanceof Error ? error.message : String(error)}. Try ${displayApiUrl}/api/execution/${action}-now`);
    } finally {
      setBusy(false);
    }
  };

  const buttonClass = compact
    ? 'inline-flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-[11px] font-black uppercase tracking-[0.18em] transition disabled:cursor-not-allowed disabled:opacity-50'
    : 'inline-flex items-center justify-center gap-2 rounded-2xl px-4 py-2 text-xs font-black uppercase tracking-[0.2em] transition disabled:cursor-not-allowed disabled:opacity-50';

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap justify-end gap-2">
        <button
          type="button"
          disabled={busy || localStopped}
          onClick={() => void sendControl('stop')}
          className={`${buttonClass} border border-rose-300/30 bg-rose-500/15 text-rose-100 hover:bg-rose-500/25`}
        >
          <CircleStop className="h-4 w-4" /> Stop Auto
        </button>
        <button
          type="button"
          disabled={busy || !localStopped}
          onClick={() => void sendControl('resume')}
          className={`${buttonClass} border border-emerald-300/30 bg-emerald-500/15 text-emerald-100 hover:bg-emerald-500/25`}
        >
          <Play className="h-4 w-4" /> Resume
        </button>
      </div>
      {message && <p className="text-right text-xs text-slate-300">{message}</p>}
    </div>
  );
}
