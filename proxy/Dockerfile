FROM python:3.12-slim

# Decision: PyYAML is the proxy's only dependency and is used for the config file
# alone. Kept to the 6.x line so a future major release cannot change what the
# pack ships; no other packages, so the image stays small and reproducible.
RUN pip install --no-cache-dir "pyyaml>=6.0.1,<7"

WORKDIR /app
COPY proxy.py /app/proxy.py
RUN mkdir -p /app/logs

# The config is read from /app/config.yml (env PROXY_CONFIG overrides the path);
# docker-compose mounts proxy/config.yml there. UPSTREAM defaults to http://glpi:80
# and PROXY_LISTEN_PORT to 8080.
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/proxy.py"]
