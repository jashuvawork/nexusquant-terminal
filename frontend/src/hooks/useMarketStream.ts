import { useEffect, useRef, useState } from 'react';
import { apiUrl, usesSameOriginApiProxy, wsUrl } from '../config/api';
import type { MarketSymbol, StreamStatus, TerminalSnapshot } from '../types';

const configuredStreamMode = (import.meta.env.VITE_STREAM_MODE ?? 'polling') as 'websocket' | 'polling' | 'hybrid';
const forceWebSocket = import.meta.env.VITE_FORCE_WEBSOCKET === 'true';
const isRailwayBackend = apiUrl.includes('.up.railway.app');
const streamMode = isRailwayBackend && !forceWebSocket ? 'polling' : configuredStreamMode;
const pollMs = Number(import.meta.env.VITE_POLL_MS ?? 1000);
const clientHeartbeatMs = Number(import.meta.env.VITE_WS_CLIENT_HEARTBEAT_MS ?? 5000);
const reconnectDelays = [1000, 2000, 4000, 8000, 10000];

interface StreamIssue {
  status: string;
  message: string;
}

function isVerifiedSnapshot(item: Partial<TerminalSnapshot> | undefined): item is TerminalSnapshot {
  const hasRealSnapshotShape = Boolean(item?.symbol && item?.timestamp && item?.marketPhase && item?.expiryState?.selectedExpiry);
  return Boolean(
    item?.expiryState?.selectedExpiry
    && (
      item?.dataSource === 'UPSTOX_REALTIME_REST'
      || item?.upstoxConnection?.marketDataVerified === true
      || hasRealSnapshotShape
    ),
  );
}

export function useMarketStream() {
  const [snapshot, setSnapshot] = useState<TerminalSnapshot | null>(null);
  const [snapshots, setSnapshots] = useState<Partial<Record<MarketSymbol, TerminalSnapshot>>>({});
  const [status, setStatus] = useState<StreamStatus>('connecting');
  const [issue, setIssue] = useState<StreamIssue | null>(null);
  const reconnectAttempts = useRef(0);
  const wsFailures = useRef(0);
  const socketIdRef = useRef(0);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | undefined;
    let reconnectTimer: number | undefined;
    let heartbeatTimer: number | undefined;
    let pollingTimer: number | undefined;
    let pollingInFlight = false;

    const applyPayload = (payload: Record<string, unknown>) => {
      if (payload.type === 'status') {
        const statusValue = payload.status as string | undefined;
        if (statusValue === 'CONNECTED' || statusValue === 'HEARTBEAT') {
          setStatus('live');
          setIssue(null);
          return;
        }
        setStatus('status');
        setIssue({ status: statusValue ?? 'STATUS', message: (payload.message as string | undefined) ?? 'Waiting for real Upstox data.' });
        return;
      }

      const incoming = payload.type === 'multi_snapshot'
        ? (payload.snapshots ?? {}) as Partial<Record<MarketSymbol, TerminalSnapshot>>
        : payload.type === 'snapshot' || payload.tradeQualityScore !== undefined
          ? { [((payload as unknown as Partial<TerminalSnapshot>).symbol ?? 'NIFTY')]: payload as unknown as TerminalSnapshot } as Partial<Record<MarketSymbol, TerminalSnapshot>>
          : {};

      const verifiedEntries = Object.entries(incoming).filter(([, item]) => isVerifiedSnapshot(item)) as Array<[MarketSymbol, TerminalSnapshot]>;
      if (verifiedEntries.length === 0) {
        setSnapshot(null);
        setSnapshots({});
        setStatus('status');
        const errors = payload.symbolErrors as Record<string, string> | undefined;
        const paused = (payload.autoTrader as { processingPaused?: boolean; pauseReason?: string } | undefined)?.processingPaused;
        setIssue({
          status: paused ? 'POST_MARKET_PAUSED' : 'WAITING_FOR_UPSTOX_DATA',
          message: paused
            ? ((payload.autoTrader as { pauseReason?: string } | undefined)?.pauseReason ?? 'Market is closed; showing analysis when the latest real snapshot is available.')
            : errors
              ? Object.entries(errors).map(([symbol, error]) => `${symbol}: ${error}`).join('; ')
              : 'No verified NIFTY/SENSEX Upstox market-data snapshots yet.',
        });
        return;
      }

      const sharedState = {
        autoTrader: payload.autoTrader,
        marketSnapshot: payload.marketSnapshot,
        institutionalReadiness: payload.institutionalReadiness,
        eventJournal: payload.eventJournal,
      };
      const sharedEntries = Object.fromEntries(Object.entries(sharedState).filter(([, value]) => value !== undefined));
      const nextSnapshots = Object.fromEntries(
        verifiedEntries.map(([symbol, item]) => [symbol, { ...item, ...sharedEntries }]),
      ) as Partial<Record<MarketSymbol, TerminalSnapshot>>;
      const displaySymbol = payload.displaySymbol as MarketSymbol | undefined;
      setSnapshots(nextSnapshots);
      setSnapshot((displaySymbol && nextSnapshots[displaySymbol]) || nextSnapshots.NIFTY || nextSnapshots.SENSEX || verifiedEntries[0][1]);
      setStatus('live');
      setIssue(null);
    };

    const startPolling = () => {
      if (disposed) return;
      try {
        socket?.close();
      } catch {
        // Ignore stale socket close.
      }
      setStatus('connecting');
      setIssue({
        status: 'HTTP_POLLING',
        message: usesSameOriginApiProxy
          ? 'Using Vercel HTTPS proxy to reach the AWS backend and avoid browser mixed-content blocking.'
          : 'Using HTTP polling fallback for stable backend connectivity.',
      });

      const poll = async () => {
        if (disposed || pollingInFlight) return;
        pollingInFlight = true;
        try {
          const response = await fetch(`${apiUrl}/api/market/snapshots`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
          const payload = await response.json() as Record<string, unknown>;
          if (!response.ok) {
            setStatus('status');
            setIssue({ status: 'UPSTOX_DATA_ERROR', message: typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail ?? payload) });
          } else {
            applyPayload(payload);
          }
        } catch (error) {
          setStatus('connecting');
          setIssue({ status: 'HTTP_POLL_RECONNECTING', message: `Polling failed: ${error instanceof Error ? error.message : String(error)}` });
        } finally {
          pollingInFlight = false;
          if (!disposed) pollingTimer = window.setTimeout(poll, pollMs);
        }
      };
      void poll();
    };

    const connectWebSocket = () => {
      if (disposed) return;
      const socketId = socketIdRef.current + 1;
      socketIdRef.current = socketId;
      try {
        socket?.close();
      } catch {
        // Ignore stale socket close.
      }

      socket = new WebSocket(wsUrl);
      socket.onopen = () => {
        if (disposed || socketId !== socketIdRef.current) return;
        reconnectAttempts.current = 0;
        wsFailures.current = 0;
        setStatus('live');
        setIssue(null);
        heartbeatTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send('PING');
        }, clientHeartbeatMs);
      };
      socket.onmessage = (event) => {
        if (disposed || socketId !== socketIdRef.current) return;
        try {
          applyPayload(JSON.parse(event.data) as Record<string, unknown>);
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
          // onclose handles reconnect.
        }
      };
      socket.onclose = () => {
        if (disposed || socketId !== socketIdRef.current) return;
        if (heartbeatTimer) window.clearInterval(heartbeatTimer);
        wsFailures.current += 1;
        if (streamMode === 'hybrid' || wsFailures.current >= 5) {
          startPolling();
          return;
        }
        const delay = reconnectDelays[Math.min(reconnectAttempts.current, reconnectDelays.length - 1)];
        reconnectAttempts.current += 1;
        setStatus('connecting');
        setIssue({ status: 'BACKEND_WS_RECONNECTING', message: `WebSocket closed. Reconnecting in ${Math.round(delay / 1000)}s.` });
        reconnectTimer = window.setTimeout(connectWebSocket, delay);
      };
    };

    window.setTimeout(() => {
      if (disposed) return;
      if (streamMode === 'polling') startPolling();
      else connectWebSocket();
    }, 0);

    return () => {
      disposed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (heartbeatTimer) window.clearInterval(heartbeatTimer);
      if (pollingTimer) window.clearTimeout(pollingTimer);
      try {
        socket?.close();
      } catch {
        // Ignore shutdown close.
      }
    };
  }, []);

  return { snapshot, snapshots, status, issue };
}
