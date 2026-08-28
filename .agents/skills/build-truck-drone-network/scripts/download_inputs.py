"""Fetch explicit public HTTPS data URLs from a reviewed catalog, create-only.

Portal-only entries remain a to-do list. No login, API key, scraping, guessing of
dataset URLs, or automatic dependency installation is attempted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from urllib.request import Request,urlopen

from portable_core import GateError,read_json,safe_path,write_json


def download(root,catalog,execute=False):
    rows=[]
    for item in catalog["datasets"]:
        url=item.get("download_url")
        if not url:
            rows.append({"role":item["role"],"status":"manual_download_required","website":item["website"],"data_type":item["data_type"]})
            continue
        parsed=urlparse(url)
        if parsed.scheme!="https" or parsed.username or parsed.password or parsed.hostname not in item["allowed_hosts"]:
            raise GateError("Only explicitly approved public HTTPS dataset hosts are allowed")
        if any(any(word in key.lower() for word in ["key","token","signature","credential"]) for key in parse_qs(parsed.query)):
            raise GateError("Do not put signed/credential-bearing URLs in a shared catalog; download manually")
        if not execute:
            rows.append({"role":item["role"],"status":"ready_for_explicit_download","website":item["website"],"path":item["path"]})
            continue
        if not item.get("license_reviewed") or not item.get("max_bytes"):
            raise GateError("Each automatic download requires license review and an explicit size ceiling")
        out=safe_path(root,item["path"])
        if out.exists() or out.with_suffix(out.suffix+".part").exists():
            raise GateError("Raw file or incomplete download already exists; not overwriting")
        out.parent.mkdir(parents=True,exist_ok=True)
        part=out.with_suffix(out.suffix+".part"); sha=hashlib.sha256(); size=0
        request=Request(url,headers={"User-Agent":"truck-drone-network-research-data-client"})
        with urlopen(request,timeout=60) as response,part.open("xb") as dst:
            final_url=urlparse(response.geturl())
            if final_url.hostname not in item["allowed_hosts"] or final_url.scheme!="https":
                raise GateError("Redirect leaves the approved public dataset hosts")
            for block in iter(lambda:response.read(1<<20),b""):
                size+=len(block)
                if size>item["max_bytes"]:
                    raise GateError("Dataset exceeds size ceiling; partial file retained for audit")
                sha.update(block); dst.write(block)
        if item.get("sha256") and item["sha256"]!=sha.hexdigest():
            raise GateError("Download checksum mismatch; partial file not published")
        part.rename(out)
        rows.append({**item,"status":"downloaded","size_bytes":size,"sha256":sha.hexdigest(),
                     "acquired_at":datetime.now(timezone.utc).isoformat()})
    return rows


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root",type=Path,required=True); p.add_argument("--catalog",type=Path,required=True)
    p.add_argument("--download",action="store_true"); p.add_argument("--report",type=Path,required=True)
    args=p.parse_args()
    if args.report.exists():
        raise GateError("Download report exists; choose a new name")
    result=download(args.project_root,read_json(args.catalog),args.download)
    write_json(args.report,result); print(json.dumps(result,indent=2))
