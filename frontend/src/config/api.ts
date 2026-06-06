const configuredApiUrl = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');
const configuredWsUrl = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws/market';

const isBrowser = typeof window !== 'undefined';
const isHttpsPage = isBrowser && window.location.protocol === 'https:';

export const usesSameOriginApiProxy = isHttpsPage && configuredApiUrl.startsWith('http:');

export const apiUrl = usesSameOriginApiProxy ? '' : configuredApiUrl;
export const displayApiUrl = usesSameOriginApiProxy && isBrowser ? window.location.origin : configuredApiUrl;
export const wsUrl = configuredWsUrl;
