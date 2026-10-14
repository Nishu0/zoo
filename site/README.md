# zoo-site

Marketing site for Zoo — self-hosted sandboxes for AI agents.

Separate from the dashboard app (`../web/`). Static output, no client framework.

## Develop

```bash
bun install
bun run dev        # http://localhost:4000
```

## Build

```bash
bun run build      # output in dist/
bun run preview
```

## Design

- Design context and principles live in [`../.impeccable.md`](../.impeccable.md).
- Tokens (palette, type, rhythm) are defined once in `src/styles/global.css`.
- Archivo Variable (expanded display) + Fragment Mono (machine facts), warm charcoal + petrol teal.
- Canonical guides live in `src/content/docs/docs/`; `../docs/development.md` documents source workflows.
- Generated references use the checked-in SDK OpenAPI artifact, registry metadata and AST-parsed CLI/SDK/configuration sources. Run `bun run docs:gen` after updating the shared schema artifact; no API server or database is started.
- Performance figures are targets until a real runtime measurement records results; no invented product behavior.

## Deploy

The site builds to plain static files in `dist/` — host anywhere (GitHub Pages, Netlify,
Cloudflare, Caddy). No server runtime required.
