# ============================================
# apps/web — Next.js production image
# Build context is the monorepo root.
# ============================================

# ============================================
# Stage 1: Install dependencies
# ============================================
FROM node:22-alpine AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app

# The lockfile lives at the workspace root; workspace manifests are needed
# so `npm ci` can resolve the workspace graph.
COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/
RUN npm ci

# ============================================
# Stage 2: Build the application
# ============================================
FROM node:22-alpine AS builder
WORKDIR /app

# Copy the whole installed tree rather than just the root node_modules: npm
# hoists everything to the root today, but a future version conflict would
# create apps/web/node_modules and silently break a root-only copy.
COPY --from=deps /app ./
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1

# NEXT_PUBLIC_* değişkenleri istemci paketine BUILD ZAMANINDA gömülür; imaj
# oluştuktan sonra runtime'da ayarlamak hiçbir şeyi değiştirmez. Bu yüzden
# build arg olarak alınıyor.
#
# Varsayılan bilinçli olarak mock backend: repoda henüz FastAPI yok ve sessizce
# boş bir adrese gitmektense çalışan bir demo üretmek daha yararlı. Gerçek
# ortama çıkarken bu değer MUTLAKA verilmeli:
#   docker compose build --build-arg NEXT_PUBLIC_API_BASE_URL=/api/v1
ARG NEXT_PUBLIC_API_BASE_URL=/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

# next.config.ts'teki rewrite hedefi. NEXT_PUBLIC_* ile aynı sebepten build
# arg: rewrites() `next build` sırasında değerlendirilir ve sonuç
# routes-manifest.json'a YAZILIR; runtime'da yeniden okunmaz. Verilmezse
# hedef "undefined/api/v1/..." olarak imaja gömülür ve her API isteği çöker.
ARG API_ORIGIN=http://api:8000
ENV API_ORIGIN=$API_ORIGIN

RUN npm run build

# ============================================
# Stage 3: Production runner
# ============================================
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# With outputFileTracingRoot set to the monorepo root, the standalone output
# mirrors the workspace layout: node_modules at the root, server entry at
# apps/web/server.js. Verified against `next build` output.
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/public ./apps/web/public

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# curl imajda yok; wget busybox ile birlikte geliyor.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget --quiet --spider http://127.0.0.1:3000/ || exit 1

CMD ["node", "apps/web/server.js"]
