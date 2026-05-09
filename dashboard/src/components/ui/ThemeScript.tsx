/**
 * ThemeScript — sets `data-theme` on <html> before paint when no
 * server-supplied value exists.
 *
 * Server-rendered: when `hasServerTheme` is true, this script is a noop
 * — the layout already wrote `data-theme` from the `theme` cookie. The
 * tiny script still runs harmlessly so we don't need conditional <head>
 * markup.
 *
 * Client-only fallback: when no cookie exists yet, this resolves the
 * theme from localStorage → prefers-color-scheme and writes both the
 * attribute (for instant paint) and the cookie (so the next SSR has it).
 */

const SCRIPT = `
(function() {
  try {
    var html = document.documentElement;
    if (html.getAttribute('data-theme')) { return; }
    var stored = localStorage.getItem('theme');
    var match = document.cookie.match(/(?:^|; )theme=(light|dark)/);
    var fromCookie = match ? match[1] : null;
    var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    var theme = fromCookie || stored || (prefersDark ? 'dark' : 'light');
    html.setAttribute('data-theme', theme);
    if (!fromCookie) {
      document.cookie = 'theme=' + theme + '; path=/; max-age=31536000; samesite=lax';
    }
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
`.trim()

export function ThemeScript({ hasServerTheme }: { hasServerTheme?: boolean } = {}) {
  // When the server already injected data-theme, the script's first
  // check short-circuits — but we still write a cookie if the user
  // landed without one (e.g. first visit).
  if (hasServerTheme) return null
  return <script dangerouslySetInnerHTML={{ __html: SCRIPT }} />
}
