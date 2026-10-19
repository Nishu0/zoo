declare module "@novnc/novnc" {
  export default class RFB extends EventTarget {
    constructor(
      target: HTMLElement,
      url: string,
      options?: { shared?: boolean; wsProtocols?: Array<string> }
    )
    scaleViewport: boolean
    resizeSession: boolean
    background: string
    showDotCursor: boolean
    disconnect(): void
    focus(): void
  }
}
