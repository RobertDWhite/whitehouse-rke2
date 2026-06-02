#!/usr/bin/env python3
import argparse
import json
import logging
import os
import sys
import time

import zmq
from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import ASYNCHRONOUS

logging.basicConfig(
    stream=sys.stdout,
    format="[%(levelname)s] [%(asctime)s] - %(message)s",
    level=logging.INFO,
)


def write_record(api, instance, topic, payload, recv_ts):
    bucket = os.environ["INFLUXDB_BUCKET"]

    if topic == "misp_json_audit" and "AuditLog" in payload:
        audit = payload["AuditLog"]
        api.write(
            bucket=bucket,
            record={
                "measurement": "audit",
                "tags": {
                    "model": str(audit.get("model", "")).lower(),
                    "action": str(audit.get("action", "")).lower(),
                    "instance": instance,
                },
                "fields": {
                    "ip": str(audit.get("ip", "")),
                    "event_id": str(audit.get("event_id", "")),
                    "model_id": str(audit.get("model_id", "")),
                    "model_title": str(audit.get("model_title", "")),
                },
            },
        )
        return

    if topic == "misp_json_event" and "Event" in payload:
        event = payload["Event"]
        record = {
            "measurement": "event",
            "tags": {"instance": instance},
            "fields": {
                "id": str(event.get("id", "")),
                "published": str(event.get("published", False)),
                "info": str(event.get("info", "")),
            },
            "time": int(float(event.get("timestamp", recv_ts)) * 1000000000),
        }
        orgc = payload.get("Orgc")
        if isinstance(orgc, dict):
            record["fields"]["org"] = str(orgc.get("name", ""))
            record["fields"]["org_id"] = str(orgc.get("id", ""))
        api.write(bucket=bucket, record=record)
        return

    if topic == "misp_json_attribute" and "Attribute" in payload:
        attr = payload["Attribute"]
        api.write(
            bucket=bucket,
            record={
                "measurement": "attribute",
                "tags": {
                    "category": str(attr.get("category", "")).lower(),
                    "type": str(attr.get("type", "")).lower(),
                    "instance": instance,
                },
                "fields": {
                    "id": str(attr.get("id", "")),
                    "event_id": str(attr.get("event_id", "")),
                    "value1": str(attr.get("value1", "")),
                    "value2": str(attr.get("value2", "")),
                    "to_ids": str(attr.get("to_ids", False)),
                },
                "time": int(float(attr.get("timestamp", recv_ts)) * 1000000000),
            },
        )
        return

    if topic == "misp_json_sighting" and "Sighting" in payload:
        sighting = payload["Sighting"]
        attr = payload.get("Attribute", {})
        api.write(
            bucket=bucket,
            record={
                "measurement": "sighting",
                "tags": {
                    "category": str(attr.get("category", "")).lower(),
                    "type": str(attr.get("type", "")).lower(),
                    "false_positive": str(sighting.get("type", "")),
                    "instance": instance,
                },
                "fields": {
                    "id": str(sighting.get("id", "")),
                    "event_id": str(sighting.get("event_id", "")),
                    "value1": str(sighting.get("value1", "")),
                    "value2": str(sighting.get("value2", "")),
                    "to_ids": str(sighting.get("to_ids", False)),
                },
                "time": int(float(sighting.get("date_sighting", recv_ts)) * 1000000000),
            },
        )
        return

    if topic == "misp_json_self" and "status" in payload:
        api.write(
            bucket=bucket,
            record={
                "measurement": "zmq_status",
                "tags": {"instance": instance},
                "fields": {"uptime": float(payload.get("uptime", 0.0))},
            },
        )


def main():
    parser = argparse.ArgumentParser(description="Push MISP ZeroMQ messages to InfluxDB")
    parser.add_argument("-id", "--instance-id", dest="instance", default="whitehouse-misp")
    parser.add_argument("-u", "--url", dest="zmq_url", default=os.environ.get("MISP_ZMQ_URL", "tcp://127.0.0.1:50000"))
    args = parser.parse_args()

    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(args.zmq_url)
    socket.setsockopt(zmq.SUBSCRIBE, b"")

    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    client = InfluxDBClient(
        url=os.environ["INFLUXDB_URL"],
        token=os.environ["INFLUXDB_TOKEN"],
        org=os.environ["INFLUXDB_ORG"],
    )
    api = client.write_api(write_options=ASYNCHRONOUS)

    logging.info("Subscribed to ZMQ: %s", args.zmq_url)

    while True:
        events = dict(poller.poll(timeout=1000))
        if socket in events and events[socket] == zmq.POLLIN:
            message = socket.recv()
            topic, _, body = message.decode("utf-8").partition(" ")
            try:
                payload = json.loads(body)
                write_record(api, args.instance, topic, payload, time.time())
            except Exception:
                logging.exception("Failed to process message on topic %s", topic)


if __name__ == "__main__":
    main()
