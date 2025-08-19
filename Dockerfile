# Dockerfile
FROM node:22-bullseye

# Minimal tools to drop privileges from root at runtime
RUN apt-get update && apt-get install -y --no-install-recommends gosu ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Pin npm inside the image for reproducibility (adjust if you prefer another)
# (Keeps host npm out of the equation.)
RUN npm i -g npm@11.5.2

# App workspace
WORKDIR /app

# Copy entrypoint
COPY docker/entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint

# Default to root so entrypoint can fix ownership, then drop to 'node'
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["dev"]

