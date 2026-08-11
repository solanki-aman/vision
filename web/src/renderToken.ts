/**
 * Carry the headless renderer's capability token onto its API calls.
 *
 * Exports are produced by loading this app in the shooter service's browser, which
 * has no session cookie. The server mints an HMAC token scoped to one canvas and
 * valid for two minutes and puts it in the page URL as `?rt=`; every API request
 * that page makes has to present it or the guard returns 404 and the export renders
 * an empty board.
 *
 * This is done by wrapping fetch once rather than by threading a parameter through
 * every call site: the token applies to the whole page load, no component should
 * have to know the app is being screenshotted, and a call added later gets it for
 * free. Only same-origin `/api/` requests are touched, and only when the token is
 * actually present — in a normal browser session this is a no-op.
 */

const RENDER_TOKEN_PARAM = "rt";

export function renderToken(): string | null {
  try {
    return new URLSearchParams(window.location.search).get(RENDER_TOKEN_PARAM);
  } catch {
    return null;
  }
}

export function forwardRenderToken(): void {
  const token = renderToken();
  if (!token) return;

  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    try {
      const url = new URL(
        typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
        window.location.origin,
      );
      if (url.origin === window.location.origin && url.pathname.startsWith("/api/")) {
        if (!url.searchParams.has(RENDER_TOKEN_PARAM)) {
          url.searchParams.set(RENDER_TOKEN_PARAM, token);
        }
        const rewritten = url.pathname + url.search;
        if (typeof input === "string" || input instanceof URL) {
          return original(rewritten, init);
        }
        return original(new Request(rewritten, input), init);
      }
    } catch {
      // A URL we cannot parse is not one we should rewrite.
    }
    return original(input as RequestInfo, init);
  };
}
