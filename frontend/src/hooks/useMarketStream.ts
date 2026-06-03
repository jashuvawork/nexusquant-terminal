import { useEffect, useState } from 'react';
import type { MarketSymbol, StreamStatus, TerminalSnapshot } from '../types';

const defaultWsUrl = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws/market';

interface StreamIssue {
  status: string;
  message: string;
}

export function useMarketStream() {
  const [snapshot, setSnapshot] = useState<TerminalSnapshot | null>(null);
  const [snapshots, setSnapshots] = useState<Partial<Record<MarketSymbol, TerminalSnapshot>>>({});
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
          if (payload.type === 'multi_snapshot') {
            const incoming = (payload.snapshots ?? {}) as Partial<Record<MarketSymbol, TerminalSnapshot>>;
            const verifiedEntries = Object.entries(incoming).filter(([, item]) =>
              item?.dataSource === 'UPSTOX_REALTIME_REST'
              && item?.upstoxConnection?.connected === true
              && item?.upstoxConnection?.marketDataVerified === true
              && item?.expiryState?.selectedExpiry,
            ) as Array<[MarketSymbol, TerminalSnapshot]>;

            if (verifiedEntries.length === 0) {
              setSnapshot(null);
              setSnapshots({});
              setStatus('status');
              setIssue({
                status: 'NON_UPSTOX_SNAPSHOT_BLOCKED',
                message: 'Backend returned no verified NIFTY/SENSEX Upstox market-data snapshots. Dummy or stale snapshots are blocked.',
              });
              return;
            }

            const nextSnapshots = Object.fromEntries(verifiedEntries) as Partial<Record<MarketSymbol, TerminalSnapshot>>;
            setSnapshots(nextSnapshots);
            setSnapshot((payload.displaySymbol && nextSnapshots[payload.displaySymbol as MarketSymbol]) || nextSnapshots.NIFTY || nextSnapshots.SENSEX || verifiedEntries[0][1]);
            setStatus('live');
            setIssue(null);
            return;
          }

          if (payload.type === 'snapshot' || payload.tradeQualityScore !== undefined) {
            const isVerifiedUpstoxSnapshot =
              payload.dataSource === 'UPSTOX_REALTIME_REST'
              && payload.upstoxConnection?.connected === true
              && payload.upstoxConnection?.marketDataVerified === true
              && payload.expiryState?.selectedExpiry;

            if (!isVerifiedUpstoxSnapshot) {
              setSnapshot(null);
              setSnapshots({});
              setStatus('status');
              setIssue({
                status: 'NON_UPSTOX_SNAPSHOT_BLOCKED',
                message: 'Backend returned a snapshot without verified Upstox market-data/expiry metadata. Dummy or stale snapshots are blocked.',
              });
              return;
            }

            const item = payload as TerminalSnapshot;
            setSnapshot(item);
            setSnapshots({ [item.symbol]: item });
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

  return { snapshot, snapshots, status, issue };
}
