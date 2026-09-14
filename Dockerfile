# Hugging Face Space (sdk: docker): rulează hub-ul central Studio Harness. Codul de administrator vine din Secretul Space-ului
# STUDIO_HARNESS_ADMIN_TOKEN (Settings → Variables and secrets; 16-512 de caractere, fără spații), niciodată din repo; fără Secret,
# hub-ul îl generează la pornire în /home/hub/state/hub-admin-token (director nepersistent pe Space, deci codul s-ar schimba la
# fiecare repornire — setează Secretul). Dispozitivele noi așteaptă aprobarea adminului din panou (fila Dispozitive).
FROM python:3.12-slim
RUN useradd --create-home --uid 1000 hub
WORKDIR /app
# Modulele Python ale hub-ului (team_hub.py importă claims, local_state, project_map și updater).
COPY scripts/team_hub.py scripts/claims.py scripts/local_state.py scripts/project_map.py scripts/updater.py ./
COPY update-channel.json ./
# Panoul web, servit la /panel din panel/index.html lângă team_hub.py.
COPY panel/index.html ./panel/
USER hub
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 STUDIO_HARNESS_STATE_DIR=/home/hub/state STUDIO_HARNESS_AUTO_UPDATE=0
EXPOSE 7860
CMD ["python3", "-u", "team_hub.py", "--listen", "0.0.0.0", "--port", "7860", "--state-dir", "/home/hub/state"]
