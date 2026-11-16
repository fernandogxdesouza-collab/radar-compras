"""Comandos remotos do painel: buscar qualquer produto, incluir, excluir ou definir alvo na lista.

Roda no GitHub Actions (workflow "Comando do painel") com as variáveis ACAO e TEXTO.
  buscar   texto                  -> docs/buscas/<slug>.json (resultado completo, igual a um item da lista)
  incluir  texto[; alvo=NN]       -> acrescenta em produtos.json
  excluir  id ou parte do nome    -> remove de produtos.json
  alvo     id ou nome; alvo=NN    -> define (ou, com 0, remove) o preço-alvo
"""
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import radar

RAIZ = Path(__file__).parent
ACAO = os.environ.get("ACAO", "").strip().lower()
TEXTO = os.environ.get("TEXTO", "").strip()
PARADAS = {"de", "da", "do", "das", "dos", "com", "para", "e", "a", "o", "em", "um", "uma", "no", "na", "por"}
TAMANHO = re.compile(r"^\d+(?:[.,]\d+)?(?:kg|g|gr|ml|l|lt|litro|litros)$")


def slug(t):
    t = "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:60].rstrip("-")


def separa_alvo(texto):
    m = re.search(r";\s*alvo\s*=\s*([\d.,]+)", texto, re.I)
    if not m:
        return texto.strip(), None
    return texto[:m.start()].strip(), float(m.group(1).replace(".", "").replace(",", ".") if "," in m.group(1) else m.group(1))


def produto_de_texto(texto, alvo=None):
    palavras = [w for w in re.findall(r"[\w.,+-]+", radar.norm(texto)) if w not in PARADAS]
    exige = [w for w in palavras if not TAMANHO.match(w)][:5] or palavras[:3]
    unidade = "kg" if radar.quantidade(texto, "kg") else ("l" if radar.quantidade(texto, "l") else None)
    return {"id": slug(texto) or "produto", "nome": texto.strip(), "buscas": [texto.strip()], "exige": exige, "exclui": [],
            "unidade": unidade, "categoria": "mercado" if unidade else "geral", "preco_alvo": alvo}


def carrega():
    return json.loads((RAIZ / "produtos.json").read_text(encoding="utf-8"))


def salva(cfg):
    (RAIZ / "produtos.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def achar(cfg, texto):
    alvo = radar.norm(texto).strip()
    exatos = [p for p in cfg["produtos"] if p["id"] == alvo or radar.norm(p["nome"]).strip() == alvo]
    if exatos:
        return exatos
    return [p for p in cfg["produtos"] if alvo and alvo in radar.norm(p["nome"] + " " + p["id"])]


def buscar(texto):
    ben = json.loads((RAIZ / "beneficios.json").read_text(encoding="utf-8"))
    arq = RAIZ / "dados" / "ofertas_super.json"
    sup = json.loads(arq.read_text(encoding="utf-8")).get("ofertas", []) if arq.exists() else []
    tab = radar.taxas_meliuz()
    p = produto_de_texto(texto)
    p["id"] = "busca-" + p["id"]
    item = radar.analisar(p, ben, tab, sup, {}, None, "", alertar=False)
    if not item["ofertas"] and len(p["exige"]) > 2:  # busca estrita demais: tenta só com as 2 primeiras palavras
        p["exige"] = p["exige"][:2]
        item = radar.analisar(p, ben, tab, sup, {}, None, "", alertar=False)
    pasta = RAIZ / "docs" / "buscas"
    pasta.mkdir(parents=True, exist_ok=True)
    s = slug(texto)
    (pasta / f"{s}.json").write_text(json.dumps({"consulta": texto, "gerado_em_ms": int(time.time() * 1000),
                                                  "gerado_em": radar.AGORA, "item": item}, ensure_ascii=False), encoding="utf-8")
    arq_ind = pasta / "indice.json"
    ind = json.loads(arq_ind.read_text(encoding="utf-8")) if arq_ind.exists() else []
    ind = [x for x in ind if x["slug"] != s]
    ind.insert(0, {"slug": s, "consulta": texto, "gerado_em": radar.AGORA, "achou": len(item["ofertas"])})
    for velho in ind[30:]:
        (pasta / f"{velho['slug']}.json").unlink(missing_ok=True)
    arq_ind.write_text(json.dumps(ind[:30], ensure_ascii=False), encoding="utf-8")
    print(f"busca '{texto}': {len(item['ofertas'])} ofertas")


def main():
    if not TEXTO:
        sys.exit("texto vazio")
    if ACAO == "buscar":
        return buscar(TEXTO)
    cfg = carrega()
    if ACAO == "incluir":
        texto, alvo = separa_alvo(TEXTO)
        p = produto_de_texto(texto, alvo)
        if any(x["id"] == p["id"] for x in cfg["produtos"]):
            sys.exit(f"'{p['id']}' já está na lista")
        cfg["produtos"].append(p)
        salva(cfg)
        print(f"incluído: {p['nome']} (exige {p['exige']}, unidade {p['unidade']})")
    elif ACAO == "excluir":
        achados = achar(cfg, TEXTO)
        if len(achados) != 1:
            sys.exit("nenhum item" if not achados else "ambíguo: " + "; ".join(x["id"] for x in achados))
        cfg["produtos"] = [x for x in cfg["produtos"] if x["id"] != achados[0]["id"]]
        salva(cfg)
        print(f"excluído: {achados[0]['nome']}")
    elif ACAO == "alvo":
        texto, valor = separa_alvo(TEXTO)
        achados = achar(cfg, texto)
        if len(achados) != 1 or valor is None:
            sys.exit("use: id ou nome; alvo=NN (item único)")
        achados[0]["preco_alvo"] = valor or None
        salva(cfg)
        print(f"alvo de {achados[0]['nome']}: {valor or 'removido'}")
    else:
        sys.exit(f"ação desconhecida: {ACAO}")


if __name__ == "__main__":
    main()
