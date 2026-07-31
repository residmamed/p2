import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The hosted build is served from paraphoria.com/p/2, not from a domain root,
// so every asset URL has to be prefixed or the page loads blank — the HTML
// arrives, then each /assets/* request 404s against the site at the root.
//
// Only the production build is prefixed. The dev server stays at "/" so
// `npm run dev` still opens at localhost:5173 with nothing to remember.
// https://vite.dev/config/
// VITE_BASE_PATH is where the app is served from. It defaults to the domain
// root, which is how it is deployed (p2.paraphoria.com).
//
// Set it to a subpath — "/p/2/" — and the build emits into a matching directory
// (dist/p/2) rather than a flat dist. That pairing matters: a static host
// resolves /p/2/assets/x.js by looking for <publish>/p/2/assets/x.js, so with a
// flat dist it finds nothing, falls through to the SPA rule, and returns
// index.html for a script request. The page then loads blank with no failing
// request to point at.
export default defineConfig(({ command }) => {
  const base = process.env.VITE_BASE_PATH ?? '/'
  return {
    base: command === 'build' ? base : '/',
    // base "/" leaves this as plain "dist"; "/p/2/" makes it "dist/p/2".
    build: { outDir: `dist${base.replace(/\/$/, '')}`, emptyOutDir: true },
    plugins: [react()],
  }
})
