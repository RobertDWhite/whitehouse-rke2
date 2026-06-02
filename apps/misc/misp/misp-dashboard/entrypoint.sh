#!/bin/sh
set -eu

cp /etc/misp-dashboard/config.cfg /opt/MISP-Dashboard/config/config.cfg

python /opt/MISP-Dashboard/zmq_subscriber.py -n "${MISP_INSTANCE_NAME}" -u "${MISP_ZMQ_URL}" &
python /opt/MISP-Dashboard/zmq_dispatcher.py &
exec python /opt/MISP-Dashboard/server.py
