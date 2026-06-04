import { useEffect, useRef, useState } from 'react';
import type { MarketSymbol, StreamStatus, TerminalSnapshot } from '../types';

const defaultWsUrl = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws/market';
const reconnectDelays = [750, 1500, 3000, 5000, 8000, 10000];

interface StreamIssue {
  status: string;
  message: string;
}

function isVerifiedSnapshot(item: Partial<TerminalSnapshot> | undefined): item is TerminalSnapshot {
  return Boolean(
    item?.dataSource === 'UPSTOX_REALTIME_REST'
    && item?.upstoxConnection?.connected === true
    && item?.upstoxConnection?.marketDataVerified === true
    && item?.expiryState?.selectedExpiry,
  );
}

export function useMarketStream() {
  const [snapshot, setSnapshot] = useState<TerminalSnapshot | null>(null);
  const [snapshots, setSnapshots] = useState<Partial<Record<MarketSymbol, TerminalSnapshot>>>({});
  const [status, setStatus] = useState<StreamStatus>('connecting');
  const [issue, setIssue] = useState<StreamIssue | null>(null);
  const retryRef = useRef(0);
  const socketIdRef = useRef(0);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | undefined;
    let reconnectTimer: number | undefined;

    const scheduleReconnect = (reason: string) => {
      if (disposed) return;
      const delay = reconnectDelays[Math.min(retryRef.current, reconnectDelays.length - 1)];
      retryRef.current += 1;
      setStatus('connecting');
      setIssue({ status: 'BACKEND_WS_RECONNECTING', message: `${reason}. Reconnecting in ${Math.round(delay / 1000)}s. No dummy data will be shown.` });
      reconnectTimer = window.setTimeout(connect, delay);
    };

    const handlePayload = (payload: Record<string, unknown>) => {
      if (payload.type === 'status') {
        if (payload.status as string | undefined === 'CONNECTED' || payload.status as string | undefined === 'HEARTBEAT') {
          setStatus('live');
          setIssue(null);
          return;
        }
        setStatus('status');
        setIssue({ status: payload.status as string | undefined ?? 'STATUS', message: payload.message as string | undefined ?? 'Waiting for real Upstox data.' });
        return;
      }

      if (payload.type === 'multi_snapshot') {
        const incoming = (payload.snapshots ?? {}) as Partial<Record<MarketSymbol, TerminalSnapshot>>;
        const verifiedEntries = Object.entries(incoming).filter(([, item]) => isVerifiedSnapshot(item)) as Array<[MarketSymbol, TerminalSnapshot]>;

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
        setSnapshot(((payload.displaySymbol as string | undefined) && nextSnapshots[payload.displaySymbol as MarketSymbol]) || nextSnapshots.NIFTY || nextSnapshots.SENSEX || verifiedEntries[0][1]);
        setStatus('live');
        setIssue(null);
        return;
      }

      if (payload.type === 'snapshot' || payload.tradeQualityScore !== undefined) {
        if (!isVerifiedSnapshot(payload)) {
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
    };

    function connect() {
      if (disposed) return;
      const socketId = socketIdRef.current + 1;
      socketIdRef.current = socketId;
      try {
        socket?.close();
      } catch {
        // Ignore stale socket close errors.
      }
      try {
        socket = new WebSocket(defaultWsUrl);
        socket.onopen = () => {
          if (disposed || socketId !== socketIdRef.current) return;
          retryRef.current = 0;
          setStatus('live');
          setIssue(null);
        };
        socket.onmessage = (event) => {
          if (disposed || socketId !== socketIdRef.current) return;
          try {
            handlePayload(JSON.parse(event.data));
          } catch (error) {
            setStatus('error');
            setIssue({ status: 'INVALID_STREAM_PAYLOAD', message: String(error) });
          }
        };
        socket.onerror = () => {
          if (disposed || socketId !== socketIdRef.current) return;
          try {
            socket?.close();
          } catch {
            // Ignore close errors; onclose schedules reconnect.
          }
        };
        socket.onclose = () => {
          if (disposed || socketId !== socketIdRef.current) return;
          scheduleReconnect('Backend WebSocket closed');
        };
      } catch (error) {
        scheduleReconnect(`Backend WebSocket init failed: ${String(error)}`);
      }
    }

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      try {
        socket?.close();
      } catch {
        // Ignore shutdown close errors.
      }
    };
  }, []);

  return { snapshot, snapshots, status, issue };
}
