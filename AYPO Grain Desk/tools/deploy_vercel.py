"""Deploy app/ to Vercel over the REST API. No Node, no CLI, no build step.

    VERCEL_TOKEN=... py -3.11 tools/deploy_vercel.py                  # preview
    VERCEL_TOKEN=... py -3.11 tools/deploy_vercel.py --prod           # production
    py -3.11 tools/deploy_vercel.py --dry-run                         # list what would go

The app is static - no bundler, no install, no build command - so a deployment is just the
files. That makes the REST API a better fit than the CLI here: it needs nothing installed
but Python, and the only secret involved is a token you can revoke.

GETTING A TOKEN
---------------
https://vercel.com/account/tokens -> Create Token. Scope it to the team that owns the
project, give it the shortest expiry you are comfortable with, and revoke it afterwards. It
never gets written to disk by this script and never leaves the machine except to
api.vercel.com.

    $env:VERCEL_TOKEN = "..."        # PowerShell
    export VERCEL_TOKEN=...          # bash

WHAT IT UPLOADS
---------------
Everything under app/ that the page actually needs. The screenshot folder, the throwaway
probe pages the test harness writes, and anything else matching SKIP below are left behind -
they are build scaffolding, not the site.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"
API = "https://api.vercel.com"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The project this is replacing. Override with --project if it is called something else.
DEFAULT_PROJECT = "aypo-grain-desk"

# Build scaffolding that lives in app/ but is not the site.
SKIP = ("_shots/*", "_probe_*.html", "_flow.html", "_shot.html", "_dump.html", "_m.html",
        "_dbg*.html", "_libtest.html", ".DS_Store", "Thumbs.db")


def files_to_send():
    out = []
    for path in sorted(APP.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(APP).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in SKIP):
            continue
        data = path.read_bytes()
        out.append({
            "rel": rel,
            "data": data,
            "sha": hashlib.sha1(data).hexdigest(),
            "size": len(data),
        })
    return out


def api(method, path, token, body=None, headers=None, raw=None):
    url = API + path
    hdr = {"Authorization": "Bearer " + token}
    if headers:
        hdr.update(headers)
    payload = raw
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode("utf-8", "replace")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit("%s %s -> HTTP %s\n%s" % (method, path, e.code, detail[:800]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", action="store_true", help="deploy to production")
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--team", help="team/scope id, if the project is not personal")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    items = files_to_send()
    total = sum(f["size"] for f in items)
    print("%d files, %.0f KB" % (len(items), total / 1024))
    for f in items:
        print("  %-38s %7.1f KB" % (f["rel"], f["size"] / 1024))

    if args.dry_run:
        print("\ndry run: nothing uploaded.")
        return 0

    token = os.environ.get("VERCEL_TOKEN", "").strip()
    if not token:
        print("\nVERCEL_TOKEN is not set, and this script will not log in for you.")
        print("Create one at https://vercel.com/account/tokens, then:")
        print('  $env:VERCEL_TOKEN = "..."   (PowerShell)')
        return 1

    q = ("?teamId=" + args.team) if args.team else ""

    # Who are we, and does the project exist? Fail here with something readable rather than
    # halfway through an upload.
    me = api("GET", "/v2/user" + (("?teamId=" + args.team) if args.team else ""), token)
    who = (me.get("user") or me).get("username") or (me.get("user") or me).get("email")
    print("\nauthenticated as %s" % who)

    try:
        proj = api("GET", "/v9/projects/" + args.project + q, token)
        print("project %s (%s)" % (proj.get("name"), proj.get("id")))
    except SystemExit as err:
        print(err)
        print("\nIf the project lives in a team, pass --team <teamId>.")
        print("List them with:  curl -H 'Authorization: Bearer $VERCEL_TOKEN' "
              "https://api.vercel.com/v2/teams")
        return 1

    # 1 — upload each file, keyed by its sha. Vercel skips ones it already holds.
    print("\nuploading…")
    for f in items:
        api("POST", "/v2/files" + q, token, raw=f["data"], headers={
            "Content-Type": "application/octet-stream",
            "x-vercel-digest": f["sha"],
            "Content-Length": str(f["size"]),
        })
        print("  ok  %-38s %s" % (f["rel"], f["sha"][:10]))

    # 2 — create the deployment from those shas. No framework, no build: the files are the
    # site, which is the whole point of this app having no bundler.
    body = {
        "name": args.project,
        "files": [{"file": f["rel"], "sha": f["sha"], "size": f["size"]} for f in items],
        "projectSettings": {
            "framework": None,
            "buildCommand": None,
            "installCommand": None,
            "outputDirectory": None,
        },
        "target": "production" if args.prod else None,
    }
    dep = api("POST", "/v13/deployments" + q, token, body=body)
    dep_id = dep.get("id")
    url = dep.get("url")
    print("\ndeployment %s" % dep_id)
    print("url        https://%s" % url)

    # 3 — wait for it to go READY, so the printed link actually works.
    deadline = time.time() + 300
    state = dep.get("readyState") or dep.get("status")
    while state in ("QUEUED", "BUILDING", "INITIALIZING") and time.time() < deadline:
        time.sleep(4)
        info = api("GET", "/v13/deployments/" + dep_id + q, token)
        state = info.get("readyState") or info.get("status")
        print("  %s" % state)
    print("\nstate: %s" % state)

    if state == "READY":
        print("\nlive at   https://%s" % url)
        if args.prod:
            aliases = dep.get("alias") or []
            for a in aliases:
                print("          https://%s" % a)
            print("\nProduction. The old dashboard is replaced.")
        else:
            print("\nPreview only. Re-run with --prod to replace the live dashboard.")
        return 0

    print("Deployment did not reach READY. Check the Vercel dashboard for the log.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
