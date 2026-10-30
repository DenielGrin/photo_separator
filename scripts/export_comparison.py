#!/usr/bin/env python3
"""Export the before/after report as one offline HTML file, with embedded photo previews and data."""

import argparse
import base64
import io
import json
import re
from pathlib import Path

from PIL import Image

from scripts.real_cover_report import render


def export(root, output):
    root, output = Path(root), Path(output)
    render(root)  # Saved evidence only; no model or network calls.
    document = (root / "comparison.html").read_text()
    manifest = json.loads((root / "manifest.json").read_text())
    baseline_assets = {p["asset"] for r in manifest["restaurants"] for p in r["baselines"] if p.get("asset")}
    refs = set(re.findall(r'src="(assets/[a-f0-9]{64}\.jpg)"', document))
    embedded = {}
    for ref in sorted(refs):
        path = (root / ref).resolve()
        if path.parent != (root / "assets").resolve():
            raise ValueError("Asset outside the report assets directory")
        with Image.open(path) as source:
            image = source.convert("RGB")
            size = 768 if ref in baseline_assets else 320
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=76 if ref in baseline_assets else 72, optimize=True)
        embedded[ref] = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
    # Store each preview once; repeated photo references reuse the same embedded data.
    document = re.sub(r'src="(assets/[a-f0-9]{64}\.jpg)"', r'data-asset="\1"', document)
    script = (
        '<script id="embedded-previews" type="application/json">'
        + json.dumps(embedded, separators=(",", ":"))
        + '</script><script>const previews=JSON.parse(document.getElementById("embedded-previews").textContent);document.querySelectorAll("img[data-asset]").forEach(img=>img.src=previews[img.dataset.asset]);</script>'
    )
    for name in ["comparison.csv", "summary.json", "analysis.json", "manifest.json"]:
        mime = "text/csv" if name.endswith(".csv") else "application/json"
        encoded = base64.b64encode((root / name).read_bytes()).decode()
        document = document.replace(
            'href="' + name + '"', 'download="' + name + '" href="data:' + mime + ";base64," + encoded + '"'
        )
    note = '<p class="hint">Автономный HTML: превью фотографий и данные встроены. Сервер и API-ключ не нужны. Для открытия исходных фотографий по внешним ссылкам требуется интернет. Встроены уменьшенные превью, не полные оригиналы.</p>'
    document = document.replace('<div class="controls">', note + '<div class="controls">', 1)
    document = document.replace("</html>", script + "</html>")
    raw = document.encode("utf-8")
    if len(raw) > 45 * 1024 * 1024:
        raise ValueError("Standalone artifact exceeds the repository file size budget")
    if re.search(
        r'(?:src|href)="(?:assets/|comparison\.csv|summary\.json|analysis\.json|manifest\.json)', document
    ):
        raise ValueError("Standalone artifact still depends on a sidecar file")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    return {
        "file": str(output),
        "bytes": len(raw),
        "embedded_previews": len(embedded),
        "restaurants": len(manifest["restaurants"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.root, args.output), ensure_ascii=False))
