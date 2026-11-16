"""Avisa (ntfy) sobre itens de supermercado muito abaixo do normal, estejam ou não na sua lista.

Lê dados/ofertas_super.json (preenchido pela tarefa agendada que lê os folhetos) e compara o preço "por" com o
preço "de" do próprio folheto. Limite em produtos.json > super > desconto_minimo (padrão 30%).
Uso: python avisos_super.py            (envia e grava dados/avisos_super.json)
     python avisos_super.py --simular  (só mostra o que enviaria)
Variável: NTFY_TOPICO.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from curl_cffi import requests as http

RAIZ = Path(__file__).parent
FUSO = timezone(timedelta(hours=-3))
HOJE = datetime.now(FUSO).strftime("%Y-%m-%d")
MAX_POR_AVISO = 8


def desconto(o):
    if not o.get("de") or o["de"] <= 0:
        return 0.0
    return (1 - o["por"] / o["de"]) * 100


def main():
    simular = "--simular" in sys.argv
    config = json.loads((RAIZ / "produtos.json").read_text(encoding="utf-8"))
    minimo = float(config.get("super", {}).get("desconto_minimo", 30))
    arq = RAIZ / "dados" / "ofertas_super.json"
    if not arq.exists():
        print("sem ofertas_super.json")
        return
    ofertas = json.loads(arq.read_text(encoding="utf-8")).get("ofertas", [])
    enviados_arq = RAIZ / "dados" / "avisos_super.json"
    enviados = json.loads(enviados_arq.read_text(encoding="utf-8")) if enviados_arq.exists() else {}
    enviados = {k: v for k, v in enviados.items() if v >= HOJE}  # esquece o que já venceu
    novos = []
    for o in ofertas:
        if o.get("validade", "9999") < HOJE:
            continue
        pct = desconto(o)
        chave = f"{o['loja']}|{o['item']}|{o['validade']}"
        if pct >= minimo and chave not in enviados:
            novos.append((pct, chave, o))
    novos.sort(key=lambda x: -x[0])
    print(f"limite {minimo:.0f}% | ofertas {len(ofertas)} | novas acima do limite: {len(novos)}")
    if not novos:
        return
    por_loja = {}
    for pct, chave, o in novos[:MAX_POR_AVISO * 3]:
        por_loja.setdefault(o["loja"], []).append((pct, o))
    for loja, lista in por_loja.items():
        lista = lista[:MAX_POR_AVISO]
        validade = max(o["validade"] for _, o in lista)
        linhas = [f"{loja}: ofertas muito abaixo do normal (até {validade[8:10]}/{validade[5:7]})"]
        for pct, o in lista:
            clube = " [clube]" if o.get("clube") else ""
            linhas.append(f"-{pct:.0f}% {o['item'][:60]}: R$ {o['por']:.2f} (de R$ {o['de']:.2f}){clube}")
        linhas.append(lista[0][1].get("link", ""))
        texto = "\n".join(linhas)
        print(texto, "\n")
        if simular:
            continue
        topico = os.environ.get("NTFY_TOPICO", "").strip()
        if not topico:
            print("NTFY_TOPICO ausente; nada enviado")
            return
        try:
            r = http.post(f"https://ntfy.sh/{topico}", data=texto.encode("utf-8"), timeout=20,
                          headers={"Priority": "4", "Tags": "shopping_cart"})
            ok = r.status_code == 200
        except Exception as erro:  # noqa: BLE001
            print(f"ntfy falhou: {type(erro).__name__}")
            ok = False
        if ok:
            for _, o in lista:
                enviados[f"{o['loja']}|{o['item']}|{o['validade']}"] = o["validade"]
    if not simular:
        enviados_arq.write_text(json.dumps(enviados, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
