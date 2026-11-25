"""Fetch and verify the small wheels required by the isolated CUDA environment."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


VERSIONS = {
    "numpy": "2.4.4",
    "Pillow": "12.2.0",
    "filelock": "4.2.12",
    "typing-extensions": "4.16.0",
    "setuptools": "81.0.0",
    "sympy": "1.14.0",
    "networkx": "3.7",
    "jinja2": "3.1.6",
    "fsspec": "2026.9.0",
    "mpmath": "1.3.0",
    "MarkupSafe": "3.0.4",
}
VISION_INDEX = "https://download.pytorch.org/whl/cu126/torchvision/"
VISION_NAME = "torchvision-0.27.1+cu126-cp312-cp312-win_amd64.whl"
CHUNK = 1024 * 1024


def request(url, *, limit, span=None):
    command = [
        "curl.exe", "--disable", "--silent", "--show-error", "--fail", "--location",
        "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "2",
        "--connect-timeout", "25", "--max-time", "75", "--max-filesize", str(limit),
        "--header", "Accept-Encoding: identity", "--write-out",
        "%{stderr}\n%{http_code}\t%header{content-range}",
    ]
    if span is not None:
        command.extend(("--range", f"{span[0]}-{span[1]}"))
    result = subprocess.run([*command, url], capture_output=True, check=False)
    details = result.stderr.decode(errors="replace").strip().splitlines()
    header = details[-1] if details else ""
    return result.returncode, header, result.stdout


def metadata(url):
    for _ in range(3):
        exit_code, header, content = request(url, limit=4 * CHUNK)
        if not exit_code and header == "200" and content:
            return content
        if header.split("\t")[0] in {"401", "403", "404", "429"}:
            break
    raise RuntimeError(f"Could not get metadata from {url} ({header}, curl {exit_code})")


def pypi_wheel(project, version):
    record = json.loads(metadata(f"https://pypi.org/pypi/{project}/{version}/json"))
    candidates = [
        row for row in record["urls"] if not row["yanked"] and
        row["filename"].endswith(("-cp312-cp312-win_amd64.whl", "-py3-none-any.whl", "-py2.py3-none-any.whl"))
    ]
    if len(candidates) != 1:
        raise ValueError(f"Expected one compatible wheel for {project} {version}")
    row = candidates[0]
    if urlsplit(row["url"]).hostname != "files.pythonhosted.org":
        raise ValueError(f"Unexpected file host for {project}")
    return row["filename"], row["url"], row["digests"]["sha256"], row["size"]


class WheelLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attributes):
        href = dict(attributes).get("href")
        if tag == "a" and href:
            self.links.append(urljoin(VISION_INDEX, href))


def vision_wheel():
    index = WheelLinks()
    index.feed(metadata(VISION_INDEX).decode())
    choices = [urlsplit(url) for url in index.links if unquote(urlsplit(url).path).endswith("/" + VISION_NAME)]
    if len(choices) != 1 or choices[0].hostname not in {"download.pytorch.org", "download-r2.pytorch.org"}:
        raise ValueError("Official TorchVision index did not return the expected Windows wheel")
    url = choices[0]
    scheme, digest = url.fragment.split("=", 1)
    if scheme != "sha256" or len(digest) != 64:
        raise ValueError("Official TorchVision SHA-256 is missing")
    content_url = urlunsplit(url._replace(fragment=""))
    exit_code, header, _ = request(content_url, limit=1, span=(0, 0))
    if exit_code or not header.startswith("206\tbytes 0-0/"):
        raise RuntimeError(f"Could not verify size for {VISION_NAME} ({header}, curl {exit_code})")
    return VISION_NAME, content_url, digest, int(header.rsplit("/", 1)[1])


def verify_wheel(path, size, expected):
    with path.open("rb") as stream:
        if path.stat().st_size != size or hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"Official wheel size or SHA-256 mismatch for {path.name}")


def transfer(root, spec):
    name, url, expected, size = spec
    if Path(name).name != name or ":" in name:
        raise ValueError("Publisher returned an unexpected filename")
    ready = root / name
    partial = root / (name + ".part")
    if not ready.exists():
        offset = partial.stat().st_size if partial.exists() else 0
        no_progress = 0
        with partial.open("ab") as stream:
            while offset < size:
                last = min(size, offset + CHUNK) - 1
                exit_code, header, content = request(url, limit=last - offset + 1, span=(offset, last))
                expected_header = f"206\tbytes {offset}-{last}/{size}"
                if exit_code in (0, 18, 28, 56) and header == expected_header and 0 < len(content) <= last - offset + 1:
                    stream.write(content)
                    stream.flush()
                    offset += len(content)
                    no_progress = 0
                else:
                    no_progress += 1
                    if no_progress == 3 or header.split("\t")[0] in {"401", "403", "404", "429"}:
                        raise RuntimeError(f"No verified range for {name} at byte {offset} ({header}, curl {exit_code})")
        verify_wheel(partial, size, expected)
        partial.rename(ready)
    verify_wheel(ready, size, expected)
    print(f"VERIFIED {name} {size} bytes {expected}", flush=True)


def main():
    root = Path(os.environ["LOCALAPPDATA"]) / "FieldFixAI/ml/envs/cv-m12-v1/support-wheelhouse-v1"
    root.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = [pool.submit(lambda p, v: transfer(root, pypi_wheel(p, v)), name, version)
                for name, version in VERSIONS.items()]
        jobs.append(pool.submit(lambda: transfer(root, vision_wheel())))
        for job in jobs:
            job.result()


if __name__ == "__main__":
    main()
