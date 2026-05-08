FROM python:3.11-slim

WORKDIR /app
COPY proxy.py .

# Drop to non-root user (uid 65534 = nobody on debian-slim)
USER 65534

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=2)"

CMD ["python", "proxy.py"]
