# Building BunkerWeb Docker Images

## Standard Build

To build BunkerWeb Docker images, use the standard Docker build command:

```bash
# Build the all-in-one image
docker build -f src/all-in-one/Dockerfile -t bunkerweb:latest .

# Build individual components
docker build -f src/bw/Dockerfile -t bunkerweb-core:latest .
docker build -f src/scheduler/Dockerfile -t bunkerweb-scheduler:latest .
docker build -f src/ui/Dockerfile -t bunkerweb-ui:latest .
docker build -f src/api/Dockerfile -t bunkerweb-api:latest .
docker build -f src/autoconf/Dockerfile -t bunkerweb-autoconf:latest .
```

## Build Requirements

- Docker 20.10 or higher
- BuildKit enabled (default in recent Docker versions)
- Internet connectivity for fetching Alpine packages

## Troubleshooting

### Build Fails with Network Errors

If you encounter network connectivity errors during the build (e.g., "TLS: server certificate not trusted", "Socket not connected"), this typically indicates that your Docker/BuildKit environment has network restrictions.

**Solution**: Ensure your Docker daemon and BuildKit have proper network access. In most standard Docker installations, this works out of the box.

### BuildKit Configuration

The Dockerfiles use standard BuildKit features and should work with the default Docker configuration. If you're in a restricted environment (e.g., corporate proxy, sandboxed CI/CD), you may need to configure your Docker daemon appropriately.

## CI/CD

The repository includes GitHub Actions workflows that successfully build all Docker images. See `.github/workflows/container-build.yml` for the reference implementation.

## Further Information

For more details on running BunkerWeb with Docker, see:
- [Official Documentation](https://docs.bunkerweb.io)
- [Docker Integration Guide](https://docs.bunkerweb.io/integrations/#docker)
- [Examples](examples/)
