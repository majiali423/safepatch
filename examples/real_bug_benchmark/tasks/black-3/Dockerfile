FROM python:3.8.20-slim@sha256:1d52838af602b4b5a831beb13a0e4d073280665ea7be7f69ce2382f29c5a613f

COPY constraints.txt /tmp/constraints.txt
RUN useradd -m -u 1000 agent \
    && pip install --no-cache-dir --retries 3 --timeout 120 -r /tmp/constraints.txt

USER agent
WORKDIR /work
