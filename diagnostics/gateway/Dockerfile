# syntax=docker/dockerfile:1

# Directory.Build.props and global.json sit at the repository root and carry the target
# framework, the analyzer settings and the SDK pin, so the build context is the repository
# root and not this directory:
#   docker build -f diagnostics/gateway/Dockerfile -t machine-agent/edge-gateway:dev .
FROM mcr.microsoft.com/dotnet/sdk:10.0.103@sha256:e362a8dbcd691522456da26a5198b8f3ca1d7641c95624fadc5e3e82678bd08a AS build
WORKDIR /src

# Manifests first so a source-only change does not re-resolve packages.
COPY global.json Directory.Build.props ./
COPY diagnostics/gateway/Gateway/Gateway.csproj \
     diagnostics/gateway/Gateway/packages.lock.json \
     ./diagnostics/gateway/Gateway/
RUN dotnet restore diagnostics/gateway/Gateway/Gateway.csproj --locked-mode

COPY diagnostics/gateway/Gateway/ ./diagnostics/gateway/Gateway/
RUN dotnet publish diagnostics/gateway/Gateway/Gateway.csproj -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0@sha256:6a94333d37514e385650a3c81a55e5350b67253dbe136e9cf17e499c35606a8c AS runtime
WORKDIR /app
COPY --from=build /app .

# The local queue's contents arrive in Task 8. Sticky-world-writable rather than chowned to
# a baked uid, because the uid this runs as is chosen at run time (plant/.env.example,
# HOST_UID): the gateway has to be whoever owns the bind-mounted pki/, since its own private
# key is mode 0600 and unreadable by anyone else. A chowned directory would break the moment
# that uid is overridden, which is always.
RUN install -d -m 1777 /queue /rejected

# Numeric so an orchestrator checking runAsNonRoot can read it (docs/ENGINEERING.md §7).
# Overridden at run time by compose's `user:` — this default cannot read a 0600 key owned by
# the host, and is not meant to.
USER 10002:10002
ENTRYPOINT ["dotnet", "Gateway.dll"]
