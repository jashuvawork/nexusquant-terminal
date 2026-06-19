const configuredApiUrl = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');
const configuredWsUrl = (import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws/market').replace(/\/$/, '');

const isBrowser = typeof window !== 'undefined';
const isHttpsPage = isBrowser && window.location.protocol === 'https:';

const productionProxyHosts = new Set(['app.nexusquant.uk']);
const forceSameOriginProxy = import.meta.env.VITE_USE_SAME_ORIGIN_PROXY === 'true';
const isKnownProductionHost = isBrowser && (
  productionProxyHosts.has(window.location.hostname)
  || window.location.hostname.endsWith('.vercel.app')
);

// HTTPS pages must not call plain-HTTP backends (mixed content). Vercel rewrites proxy /api to AWS.
export const usesSameOriginApiProxy = isHttpsPage && (
  forceSameOriginProxy
  || configuredApiUrl.startsWith('http:')
  || isKnownProductionHost
);

export const apiUrl = usesSameOriginApiProxy ? '' : configuredApiUrl;
export const displayApiUrl = usesSameOriginApiProxy && isBrowser ? window.location.origin : configuredApiUrl;

const wsProtocol = isBrowser && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
export const wsUrl = usesSameOriginApiProxy && isBrowser
  ? `${wsProtocol}//${window.location.host}/ws/market`
  : configuredWsUrl;
