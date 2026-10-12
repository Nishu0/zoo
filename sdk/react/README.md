# @zoo/react

A sandbox's live screen in a React app, from a Zoo viewer link.

```tsx
import { useRef } from "react"
import { ZooViewer, type ZooViewerHandle } from "@zoo/react"

// url: from POST /sandboxes/{id}/viewer-links, made on your server with your API key
export function Screen({ url }: { url: string }) {
  const viewer = useRef<ZooViewerHandle>(null)
  return (
    <>
      <ZooViewer ref={viewer} url={url} onEvent={(e) => console.log(e)} />
      <button onClick={() => viewer.current?.takeover()}>Take over</button>
      <button onClick={() => viewer.current?.release()}>Hand back</button>
    </>
  )
}
```

The Zoo API only lets the origins in `ZOO_EMBED_ORIGINS` frame its viewer, so add your app's origin there.

Without React, use the link's url as an iframe's `src`; the page posts `{source: "zoo", type}` messages
(`connect`, `disconnect`, `takeover`, `release`, `agent` with `state`) to its parent and takes `takeover` and `release`.
