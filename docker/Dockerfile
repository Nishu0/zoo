# zoo-guest: the in-sandbox agent the API sends tool calls to (see go/guest/)
FROM --platform=$BUILDPLATFORM golang:1.26-bookworm AS guest
ARG TARGETOS TARGETARCH
WORKDIR /src
COPY go/guest/go.mod go/guest/go.sum ./
RUN go mod download
COPY go/guest/ ./
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /zoo-guest .

FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    xfce4 \
    xfce4-terminal \
    xvfb \
    x11vnc \
    supervisor \
    firefox-esr \
    dbus-x11 \
    at-spi2-core \
    earlyoom \
    nftables \
    curl \
    ca-certificates \
    iproute2 \
    apparmor \
    wmctrl \
    procps \
    && rm -rf /var/lib/apt/lists/*

# no thumbnailer or power manager in a sandbox; caches built here, not at each first boot
RUN apt-get purge -y tumbler xfce4-power-manager ipp-usb \
    && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f \
    && for theme in /usr/share/icons/*/; do if [ -f "$theme/index.theme" ]; then gtk-update-icon-cache -f "$theme"; fi; done

# the panel's default layout, so it starts without asking which one to use
RUN mkdir -p /etc/xdg/xfce4/xfconf/xfce-perchannel-xml \
    && cp /etc/xdg/xfce4/panel/default.xml /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml

# Firefox without first-run pages, update checks or default-browser prompts
COPY firefox-policies.json /usr/lib/firefox-esr/distribution/policies.json

RUN useradd -m -s /bin/bash zoo
# a profile made at build, so the first launch doesn't make one
RUN su zoo -c "HOME=/home/zoo firefox-esr --headless -CreateProfile default" >/dev/null 2>&1

COPY --from=guest /zoo-guest /usr/local/bin/zoo-guest
COPY supervisord.conf /etc/supervisor/conf.d/desktop.conf
COPY sandbox-entrypoint.sh /usr/local/bin/sandbox-entrypoint
RUN chmod 755 /usr/local/bin/sandbox-entrypoint

CMD ["/usr/local/bin/sandbox-entrypoint"]