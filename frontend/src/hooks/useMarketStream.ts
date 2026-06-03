import { useEffect, useState } from 'react';
import type { StreamStatus, TerminalSnapshot } from '../types';

const defaultWsUrl = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws/market';

interface StreamIssue {
  status: string;
  message: string;
}

export function useMarketStream() {
  const [snapshot, setSnapshot] = useState<TerminalSnapshot | null>(null);
  const [status, setStatus] = useState<StreamStatus>('connecting');
  const [issue, setIssue] = useState<StreamIssue | null>(null);

  useEffect(() => {
    let closed = false;
    let socket: WebSocket | undefined;

    try {
      socket = new WebSocket(defaultWsUrl);
      socket.onopen = () => {
        if (closed) return;
        setStatus('live');
        setIssue(null);
      };
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === 'status') {
            setStatus('status');
            setIssue({ status: payload.status ?? 'STATUS', message: payload.message ?? 'Waiting for real Upstox data.' });
            return;
          }
          if (payload.type === 'snapshot' || payload.tradeQualityScore !== undefined) {
            const isVerifiedUpstoxSnapshot =
              payload.dataSource === 'UPSTOX_REALTIME_REST'
              && payload.upstoxConnection?.connected === true
              && payload.portfolio?.fundsSource === 'upstox'
              && payload.expiryState?.selectedExpiry;

            if (!isVerifiedUpstoxSnapshot) {
              setSnapshot(null);
              setStatus('status');
              setIssue({
                status: 'NON_UPSTOX_SNAPSHOT_BLOCKED',
                message: 'Backend returned a snapshot without verified Upstox funds/expiry metadata. Dummy or stale snapshots are blocked.',
              });
              return;
            }

            setSnapshot(payload as TerminalSnapshot);
            setStatus('live');
            setIssue(null);
          }
        } catch (error) {
          setStatus('error');
          setIssue({ status: 'INVALID_STREAM_PAYLOAD', message: String(error) });
        }
      };
      socket.onerror = () => {
        if (closed) return;
        setStatus('error');
        setIssue({ status: 'BACKEND_WS_ERROR', message: 'Could not connect to backend WebSocket. Check VITE_WS_URL and Render service health.' });
      };
      socket.onclose = () => {
        if (closed) return;
        setStatus('error');
        setIssue({ status: 'BACKEND_WS_CLOSED', message: 'Backend WebSocket closed. No local dummy market data will be shown.' });
      };
    } catch (error) {
      window.setTimeout(() => {
        if (closed) return;
        setStatus('error');
        setIssue({ status: 'BACKEND_WS_INIT_FAILED', message: String(error) });
      }, 0);
    }

    return () => {
      closed = true;
      socket?.close();
    };
  }, []);

  return { snapshot, status, issue };
}
