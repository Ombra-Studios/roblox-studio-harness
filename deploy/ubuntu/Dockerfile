# Alternativă la systemd: docker build -f deploy/ubuntu/Dockerfile -t studio-harness-hub .
# docker run -d --name studio-harness-hub -p 127.0.0.1:34880:34880 -v studio-harness-hub:/data studio-harness-hub
# Codul de administrator se creează la prima pornire în volum (/data/hub-admin-token: docker exec studio-harness-hub cat /data/hub-admin-token)
# sau vine din mediul containerului: docker run ... -e STUDIO_HARNESS_ADMIN_TOKEN ... (variabila din gazdă, 16-512 de caractere, fără spații).
# Dispozitivele noi așteaptă aprobarea din panou (fila Dispozitive) sau: docker exec studio-harness-hub python3 team_hub.py --state-dir /data --approve-pending
FROM python:3.12-slim
RUN useradd --system --create-home --home-dir /data studio-harness
WORKDIR /opt/studio-harness
# Modulele Python ale hub-ului (team_hub.py importă claims, local_state, project_map și updater).
COPY scripts/team_hub.py scripts/claims.py scripts/local_state.py scripts/project_map.py scripts/updater.py ./
COPY update-channel.json ./
# Panoul web, servit la /panel din panel/index.html lângă team_hub.py.
COPY panel/index.html ./panel/
USER studio-harness
VOLUME ["/data"]
EXPOSE 34880
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:34880/healthz', timeout=3).status == 200 else 1)"
CMD ["python3", "-u", "team_hub.py", "--listen", "0.0.0.0", "--port", "34880", "--state-dir", "/data"]
