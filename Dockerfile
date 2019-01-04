 FROM archlinux/base as systemd
 ENV container docker
 STOPSIGNAL SIGRTMIN+3
 CMD [ "/lib/systemd/systemd", \
       "--default-standard-output=journal+console", \
       "--default-standard-error=journal+console"]

FROM systemd as kevin-standalone
RUN pacman -Sy --noconfirm python python-pip
RUN mkdir -p /etc/kevin/falk.d
RUN mkdir -p /etc/kevin/projects
COPY docker/falk.conf /etc/kevin/falk.conf
COPY docker/kevin.conf /etc/kevin/kevin.conf
COPY etc/falk.service /etc/systemd/system
COPY etc/kevin.service /etc/systemd/system
COPY docker/project.conf /etc/kevin/projects/project.conf
COPY etc/tmpfiles.d/kevin.conf /usr/lib/tmpfiles.d/
COPY kevin /usr/lib/python3.7/site-packages/kevin
COPY falk /usr/lib/python3.7/site-packages/falk
COPY chantal /usr/lib/python3.7/site-packages/chantal
COPY mandy /usr/lib/python3.7/site-packages/mandy
COPY requirements.txt /tmp/
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt
RUN systemctl enable kevin falk
RUN useradd -s /usr/bin/nologin kevin
RUN useradd -s /usr/bin/nologin -g 999 -G kevin falk
#RUN mkdir -p /run/kevin/ && chown -R kevin:kevin /run/ && chmod -R 770 /run/kevin
RUN mkdir -p /srv/www && chown -R kevin:kevin /srv/www
RUN systemctl mask getty
RUN systemctl mask console-getty
RUN systemctl mask systemd-logind

FROM kevin-standalone as kevin-inception
COPY docker/kevin-simulator.service /etc/systemd/system
COPY falk/vm/tests/ubuntu_ping /ubuntu_ping
RUN systemctl enable kevin-simulator
RUN pacman -Sy --noconfirm git
