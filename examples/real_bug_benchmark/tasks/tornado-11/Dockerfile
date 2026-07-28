FROM python:3.7.17-slim@sha256:b53f496ca43e5af6994f8e316cf03af31050bf7944e0e4a308ad86c001cf028b

COPY constraints.txt /tmp/constraints.txt
RUN useradd -m -u 1000 agent \
    && pip install --no-cache-dir -r /tmp/constraints.txt

USER agent
WORKDIR /work
