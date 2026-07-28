FROM python:3.6.15-slim@sha256:2cfebc27956e6a55f78606864d91fe527696f9e32a724e6f9702b5f9602d0474

COPY constraints.txt /tmp/constraints.txt
RUN useradd -m -u 1000 agent \
    && pip install --no-cache-dir -r /tmp/constraints.txt

USER agent
WORKDIR /work
