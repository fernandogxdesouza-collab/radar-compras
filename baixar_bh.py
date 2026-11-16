"""Baixa o folheto de varejo do Supermercados BH (Matozinhos) e guarda em dados/bh/.

Usa o mesmo endereço público que o site supermercadosbh.com.br chama ao abrir a página de folhetos.
Grava dados/bh/folheto.pdf e dados/bh/folheto.json (título, validade, url, hash). A rotina que lê
os folhetos usa esses arquivos do repositório, sem acessar o BH. Sai com erro se o BH não responder.
"""
import hashlib
import json
import pathlib
import re
import sys
import urllib.request
from datetime import datetime, timezone

API = "https://superbh.app/site-api/api/mov-folheto/vigente"
CORPO = {"municipio": {"value": "MATOZINHOS@MG", "text": "MATOZINHOS - (MG)"}}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
PASTA = pathlib.Path(__file__).parent / "dados" / "bh"


def pede(url, corpo=None, tentativas=3):
    ultimo = None
    for _ in range(tentativas):
        try:
            req = urllib.request.Request(url, data=json.dumps(corpo).encode() if corpo else None,
                                         headers={"User-Agent": UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise SystemExit(f"BH não respondeu ({url[:60]}): {ultimo}")


def main():
    resp = json.loads(pede(API, CORPO))
    if not resp.get("isValid") or not isinstance(resp.get("data"), list):
        raise SystemExit("resposta inesperada do BH: " + str(resp)[:200])
    varejo = [f for f in resp["data"] if str(f.get("titulo", "")).lower().startswith("varejo")]
    if not varejo:
        raise SystemExit("nenhum folheto 'Varejo' vigente para Matozinhos")
    f = varejo[0]
    datas = re.findall(r"(\d{2})/(\d{2})/(\d{4})", f.get("descricao") or "")
    validade = "-".join(reversed(datas[-1])) if datas else None
    pdf = pede(f["folheto"])
    if not pdf.startswith(b"%PDF"):
        raise SystemExit("o arquivo baixado não é PDF")
    PASTA.mkdir(parents=True, exist_ok=True)
    (PASTA / "folheto.pdf").write_bytes(pdf)
    meta = {"titulo": f.get("titulo"), "validade": validade, "url": f["folheto"], "sha256": hashlib.sha256(pdf).hexdigest(),
            "tamanho": len(pdf), "baixado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    (PASTA / "folheto.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf8")
    print(f"ok: {meta['titulo']} até {validade}, {len(pdf)//1024} KB")


if __name__ == "__main__":
    sys.exit(main())
