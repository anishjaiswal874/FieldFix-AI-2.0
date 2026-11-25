"""Resume the verified Windows CUDA wheel transfer in the new CV environment.

Run this module only after stopping any earlier transfer in this wheelhouse.
It retains all prior receipts and verifies PyTorch's published SHA-256 before
publishing an installable wheel.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib,json,os,subprocess,time,threading
from pathlib import Path
env=Path(os.environ["LOCALAPPDATA"])/"FieldFixAI/ml/envs/cv-m12-v1"
root=env/"wheelhouse-v3"
root.mkdir(exist_ok=True)
name="torch-2.12.1+cu126-cp312-cp312-win_amd64.whl"
encoded=name.replace("+","%2B")
hosts=[
    (f"https://download.pytorch.org/whl/cu126/{encoded}",'"624db78e464fe224567b65e9ef22c60a"'),
    (f"https://download-r2.pytorch.org/whl/cu126/{encoded}",'"2f61aa8aeefbe4be4c8ccf0be92c3e97-13"'),
]
expected="71ee0eca3bde3a4067dfbc28c705b914d0773edd29ac4a634ad5e7a07f336aa1"
total=2620768231
block=4*1024**2
part=root/(name+".part")
dest=root/name
journal=root/"ranges.jsonl"
lock=threading.Lock()
saved={}
last_report=0
def filehash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()
def check(path):
    if path.stat().st_size!=total or filehash(path)!=expected:
        raise ValueError("Assembled wheel SHA-256 differs from the official index")
if dest.exists():
    check(dest)
    print(f"VERIFIED_WHEEL={dest}",flush=True)
    raise SystemExit(0)
def record(offset, data, source):
    global bytes_done,last_report
    if not data:
        return
    base=offset//block*block
    with lock:
        if offset!=base+saved.get(base,0) or offset+len(data)>min(base+block,total):
            raise ValueError("Refusing overlap or noncontiguous wheel receipt")
        with part.open("r+b") as stream:
            stream.seek(offset)
            stream.write(data)
            stream.flush()
        receipt={"offset":offset,"count":len(data),"sha256":hashlib.sha256(data).hexdigest(),"source":source}
        with journal.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(receipt,sort_keys=True)+"\n")
            stream.flush()
            os.fsync(stream.fileno())
        saved[base]=saved.get(base,0)+len(data)
        bytes_done+=len(data)
        if bytes_done-last_report>=32*1024**2 or bytes_done==total:
            print(f"Wheel bytes saved with checked ranges: {bytes_done:,}/{total:,}; elapsed {time.monotonic()-started:.0f}s",flush=True)
            last_report=bytes_done
started=time.monotonic()
bytes_done=0
if not part.exists():
    if journal.exists():
        raise ValueError("Partial wheel missing while journal is present")
    with part.open("xb") as stream:
        stream.truncate(total)
    journal.touch(exist_ok=False)
    older=env/"wheelhouse-v2"
    old_journal=older/"torch-ranges.jsonl"
    if old_journal.is_file():
        with (older/(name+".part")).open("rb") as stream:
            for line in old_journal.read_bytes().splitlines():
                r=json.loads(line)
                stream.seek(r["offset"])
                p=stream.read(r["count"])
                if (r["offset"]%block or len(p)!=r["count"] or hashlib.sha256(p).hexdigest()!=r["sha256"]
                        or r["etag"]!=hosts[0][1]):
                    raise ValueError("Earlier verified range did not pass digest recheck")
                record(r["offset"],p,"verified v2 resume transfer")
else:
    if part.stat().st_size!=total:
        raise ValueError("Wrong partial wheel size")
    with part.open("rb") as stream:
        for line in journal.read_bytes().splitlines():
            r=json.loads(line)
            offset,count=r["offset"],r["count"]
            base=offset//block*block
            if offset!=base+saved.get(base,0) or not 0<count<=min(base+block,total)-offset:
                raise ValueError("Invalid range journal")
            stream.seek(offset)
            if hashlib.sha256(stream.read(count)).hexdigest()!=r["sha256"]:
                raise ValueError(f"Stored block failed verification at {offset}")
            saved[base]=saved.get(base,0)+count
            bytes_done+=count
print(f"Restored {bytes_done:,} verified bytes from previous receipts",flush=True)
def fetch(base):
    end=min(total,base+block)-1
    offset=base+saved.get(base,0)
    idle=0
    while offset<=end:
        url,tag=hosts[(base//block+idle)%len(hosts)]
        requested=end-offset+1
        args=["curl.exe","--disable","--silent","--show-error","--location","--fail",
              "--proto","=https","--proto-redir","=https","--max-redirs","3","--connect-timeout","20",
              "--max-time","130","--range",f"{offset}-{end}","--max-filesize",str(requested),
              "--header","Accept-Encoding: identity","--write-out",
              "%{stderr}\n%{http_code}\t%header{content-range}\t%header{etag}",url]
        res=subprocess.run(args,capture_output=True)
        signed=res.stderr.decode(errors="replace").strip()
        expect=f"206\tbytes {offset}-{end}/{total}\t{tag}"
        last=signed.splitlines()[-1] if signed else ""
        if res.returncode in (0,18,28,56) and last==expect and 0<len(res.stdout)<=requested:
            record(offset,res.stdout,url)
            offset+=len(res.stdout)
            idle=0
        elif last.split("\t")[0] in {"401","403","404","429"}:
            raise RuntimeError(f"Publisher refused wheel range at {offset}: {signed}")
        else:
            idle+=1
            if idle==4:
                raise RuntimeError(f"No verified bytes from either official endpoint for offset {offset}: {signed}")
            time.sleep(2)
blocks=[base for base in range(0,total,block) if saved.get(base,0)<min(block,total-base)]
with ThreadPoolExecutor(max_workers=28) as pool:
    pending=[pool.submit(fetch,base) for base in blocks]
    try:
        for f in as_completed(pending):
            f.result()
    except BaseException:
        for f in pending:
            f.cancel()
        raise
if bytes_done!=total:
    raise ValueError("Wheel reconstruction incomplete")
check(part)
part.rename(dest)
print(f"VERIFIED_WHEEL={dest}",flush=True)
