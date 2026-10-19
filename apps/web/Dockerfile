FROM oven/bun:1 AS build
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY . .
RUN bun run build

FROM oven/bun:1
WORKDIR /app
# the server bundle imports its dependencies (react-dom/server and the rest) at runtime; srvx serves it, installed
# here so the container starts without reaching a registry
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile --production && bun add --global srvx@1.0.5
COPY --from=build /app/dist ./dist
ENV PATH="/root/.bun/bin:$PATH"
EXPOSE 3000
CMD ["srvx", "--prod", "--port", "3000", "-s", "../client", "dist/server/server.js"]
