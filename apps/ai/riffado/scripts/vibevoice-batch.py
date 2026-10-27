"""Backfill: transcribe older Riffado recordings with VibeVoice and re-summarize them.

Usage (from a machine with cluster access; see ../README.md):
    kubectl -n ai-stack port-forward svc/ollama-5090 11434:11434 &
    python3 vibevoice-batch.py                 # every recording not yet on VibeVoice
    python3 vibevoice-batch.py <id> [<id>...]  # just these, even if already done

New recordings no longer need this: VibeVoice is the default provider for Plaud
sync. A detached Bun job inside the Riffado pod posts the audio straight to
VibeVoice and saves the text through Riffado's own from-browser endpoint;
summaries go through /summary the same way. Jobs survive dropped exec streams.

It signs a short-lived Better Auth session for the household account with
BETTER_AUTH_SECRET and deletes it at the end. It unloads ollama-5090 before
each transcription.
"""
import base64, hashlib, hmac, json, secrets, subprocess, sys, time, urllib.parse, urllib.request

KC = []  # set KUBECONFIG to a VIP kubeconfig if a control-plane node is flaky
PG = ["kubectl", *KC, "-n", "riffado", "exec", "-i", "riffado-postgres-0", "--",
      "psql", "-U", "postgres", "-d", "riffado", "-tAq", "-v", "ON_ERROR_STOP=1"]
EXEC = ["kubectl", *KC, "-n", "riffado", "exec", "-i", "deploy/riffado", "-c", "riffado", "--"]
OLLAMA = "http://127.0.0.1:11434"

JOB_JS = r"""
const job = JSON.parse(await Bun.file("/tmp/vvjob/job.json").text());
const ip = Object.values(require("os").networkInterfaces()).flat()
  .find(a => a.family === "IPv4" && !a.internal).address;
const self = `http://${ip}:3000`;
const out = (o) => Bun.write("/tmp/vvjob/result.json", JSON.stringify(o));
try {
  if (job.kind === "transcribe") {
    const form = new FormData();
    form.append("file", Bun.file(job.audio), job.audio.split("/").pop());
    form.append("model", "vibevoice-asr");
    form.append("response_format", "verbose_json");
    const vv = await fetch("http://vibevoice.riffado.svc.cluster.local/v1/audio/transcriptions", {
      method: "POST", body: form, timeout: false,
      headers: {Authorization: `Bearer ${job.key}`}});
    const vvBody = await vv.text();
    if (!vv.ok) { await out({status: vv.status, step: "vibevoice", body: vvBody.slice(0, 500)}); process.exit(0); }
    const d = JSON.parse(vvBody);
    const r = await fetch(`${self}/api/recordings/${job.id}/transcription/from-browser`, {
      method: "POST", timeout: false,
      headers: {"Content-Type": "application/json", Cookie: job.cookie},
      body: JSON.stringify({text: d.text, model: "vibevoice-asr", detectedLanguage: d.language || null})});
    const speakers = new Set((d.text.match(/^\[[\d:]+\] (Speaker \d+)$/gm) || []).map(l => l.split("] ")[1]));
    await out({status: r.status, step: "save", chars: d.text.length, speakers: speakers.size,
               body: r.ok ? "" : (await r.text()).slice(0, 500)});
  } else {
    const r = await fetch(`${self}/api/recordings/${job.id}/summary`, {
      method: "POST", timeout: false,
      headers: {"Content-Type": "application/json", Cookie: job.cookie}, body: "{}"});
    await out({status: r.status, step: "summary", body: r.ok ? "" : (await r.text()).slice(0, 500)});
  }
} catch (e) { await out({status: 0, step: "exception", body: String(e)}); }
"""


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def retry(fn, tries=20):
    for i in range(tries):
        try:
            return fn()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
            if i == tries - 1:
                raise
            time.sleep(15)


def sql(q):
    return retry(lambda: subprocess.run(PG, input=q, capture_output=True, text=True, check=True,
                                        timeout=60).stdout.strip())


def pod(cmd, stdin=""):
    return retry(lambda: subprocess.run(EXEC + cmd, input=stdin, capture_output=True, text=True,
                                        check=True, timeout=120).stdout)


def secret(key):
    out = subprocess.run(["kubectl", *KC, "-n", "riffado", "get", "secret", "riffado-env",
                          "-o", f"jsonpath={{.data.{key}}}"], capture_output=True, text=True, check=True)
    return base64.b64decode(out.stdout).decode()


def ollama(path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(OLLAMA + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def unload_ollama():
    try:
        for m in ollama("/api/ps").get("models", []):
            ollama("/api/generate", {"model": m["name"], "keep_alive": 0})
            log("  unloaded ollama", m["name"])
    except Exception as e:
        log("  ollama unload skipped:", e)


def vibevoice_busy():
    js = ('const r = await fetch("http://vibevoice.riffado.svc.cluster.local/health");'
          'console.log(await r.text());')
    return json.loads(pod(["bun", "-e", js]).strip().splitlines()[-1])["busy"]


def run_job(job, max_minutes):
    pod(["sh", "-c", "mkdir -p /tmp/vvjob && rm -f /tmp/vvjob/result.json && cat > /tmp/vvjob/job.js"], JOB_JS)
    pod(["sh", "-c", "cat > /tmp/vvjob/job.json"], json.dumps(job))
    pod(["sh", "-c", "nohup bun /tmp/vvjob/job.js > /tmp/vvjob/job.log 2>&1 &"])
    t0 = time.time()
    while time.time() - t0 < max_minutes * 60:
        time.sleep(20)
        try:
            out = pod(["sh", "-c", "cat /tmp/vvjob/result.json 2>/dev/null || true"]).strip()
        except subprocess.CalledProcessError:
            continue
        if out:
            pod(["sh", "-c", "rm -f /tmp/vvjob/job.json"])
            res = json.loads(out)
            res["minutes"] = round((time.time() - t0) / 60, 1)
            return res
    return {"status": 0, "step": "timeout", "minutes": max_minutes}


def main():
    users = sql("select id from users").splitlines()
    if len(users) != 1:
        sys.exit(f"expected one Riffado user, found {len(users)}")
    user_id = users[0]
    sql("delete from sessions where user_agent = 'vibevoice-batch'")
    token = secrets.token_urlsafe(24)
    sid = "batch-" + secrets.token_hex(6)
    sql(f"insert into sessions (id, token, user_id, expires_at, user_agent) values "
        f"('{sid}', '{token}', '{user_id}', now() + interval '12 hours', 'vibevoice-batch')")
    sig = base64.b64encode(hmac.new(secret("BETTER_AUTH_SECRET").encode(), token.encode(),
                                    hashlib.sha256).digest()).decode()
    cookie = "__Secure-better-auth.session_token=" + urllib.parse.quote(f"{token}.{sig}", safe="")
    key = secret("VIBEVOICE_API_KEY")
    results = {}
    try:
        recs = [l.split("|") for l in sql(
            "select r.id, r.duration/60000, r.storage_path, coalesce(t.model, '') from recordings r "
            "left join transcriptions t on t.recording_id = r.id and t.user_id = r.user_id "
            "where r.deleted_at is null order by r.start_time").splitlines()]

        force = set(sys.argv[1:])
        if force:
            recs = [r for r in recs if r[0] in force]
        for rid, mins, path, model in recs:
            if model == "vibevoice-asr" and rid not in force:
                log(f"transcribe {rid}: already vibevoice")
                results[rid] = "ok"
                continue
            while vibevoice_busy():
                log("  vibevoice busy, waiting")
                time.sleep(30)
            unload_ollama()
            log(f"transcribe {rid} ({mins} min)")
            res = run_job({"kind": "transcribe", "id": rid, "audio": f"/app/audio/{path}",
                           "key": key, "cookie": cookie}, max_minutes=int(mins) + 60)
            log(f"  {res}")
            results[rid] = "ok" if res.get("status") == 200 else f"transcribe {res.get('status')}"


        for rid, *_ in recs:
            if results[rid] != "ok":
                continue
            log(f"summarize {rid}")
            res = run_job({"kind": "summary", "id": rid, "cookie": cookie}, max_minutes=40)
            log(f"  {res}")
            if res.get("status") != 200:
                results[rid] = f"summary {res.get('status')}"
        log("RESULTS", json.dumps(results))
    finally:
        sql(f"delete from sessions where id = '{sid}'")
        log("session removed")


if __name__ == "__main__":
    main()
