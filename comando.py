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


MAX_LISTA = 30


def itens_da_lista(texto):
    """Uma linha por item (ou separados por '|'). Aceita marcadores ('- ', '* ', '1. ', '2) ') e ignora linhas vazias."""
    itens = []
    for linha in re.split(r"[\n\r|]+", texto):
        linha = re.sub(r"^\s*(?:[-*•]|\d+\s*[.)])\s+", "", linha).strip()
        if len(linha) >= 2:
            itens.append(linha)
    if len(itens) > MAX_LISTA:
        sys.exit(f"lista com {len(itens)} itens; o limite por vez é {MAX_LISTA}")
    return itens


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


LOJAS_LINKS = [
    ("Mercado Livre", "https://lista.mercadolivre.com.br/{s}"),
    ("Shopee", "https://shopee.com.br/search?keyword={q}"),
    ("Amazon", "https://www.amazon.com.br/s?k={q}"),
    ("Magazine Luiza", "https://www.magazineluiza.com.br/busca/{q}/"),
    ("Americanas", "https://www.americanas.com.br/busca/{s}"),
    ("KaBuM!", "https://www.kabum.com.br/busca/{s}"),
    ("Carrefour", "https://www.carrefour.com.br/busca/{q}"),
    ("Pão de Açúcar", "https://www.paodeacucar.com/busca?terms={q}"),
    ("Jacotei", "https://www.jacotei.com.br/busca?q={q}"),
    ("Zoom", "https://www.zoom.com.br/search?q={q}"),
    ("Buscapé", "https://www.buscape.com.br/search?q={q}"),
    ("Bondfaro", "https://www.bondfaro.com.br/search?q={q}"),
]
LIDAS_PELO_ROBO = "Zoom (que traz Magazine Luiza, Americanas, KaBuM!, Fast Shop e parte da Amazon)"
LOJAS_FISICAS = ["Mart Minas (Pedro Leopoldo)", "Supermercados BH (Matozinhos)", "Supermercados Paranaíba (Matozinhos)"]


def links_de_pesquisa(texto):
    from urllib.parse import quote_plus
    return [{"loja": n, "url": u.format(q=quote_plus(texto), s=slug(texto))} for n, u in LOJAS_LINKS]


def casa_encarte(tokens, item):
    """O item do folheto parece o que foi pesquisado? Exige a maioria das palavras (e pelo menos 2, ou a única)."""
    t = radar.norm(item).replace(" ", "")
    acertos = sum(1 for k in tokens if k in t)
    return acertos >= max(1, -(-len(tokens) * 6 // 10)) and (acertos >= 2 or len(tokens) == 1)


def encartes(texto, p, sup, valor_online):
    """Comparação com os folhetos das redes locais: melhores itens parecidos por rede, ou aviso de que não achou."""
    tokens = [k.replace(" ", "") for k in p["exige"]]
    vistas = sorted({o["loja"] for o in sup} | set(LOJAS_FISICAS))
    saida = []
    for loja in vistas:
        itens = []
        for o in sup:
            if o["loja"] != loja or o.get("validade", "9999") < radar.HOJE or not casa_encarte(tokens, o["item"]):
                continue
            qtd = radar.quantidade(o["item"], p["unidade"]) if p["unidade"] else None
            if p["unidade"] and not qtd:
                continue
            valor = o["por"] / qtd if qtd else o["por"]
            itens.append({"item": o["item"], "de": o.get("de"), "por": o["por"], "validade": o["validade"], "clube": bool(o.get("clube")),
                          "valor": round(valor, 2), "link": o.get("link", ""),
                          "vs_online": round((valor / valor_online - 1) * 100, 1) if valor_online else None})
        itens.sort(key=lambda x: x["valor"])
        saida.append({"loja": loja, "achou": len(itens), "itens": itens[:3]})
    return saida


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
    supermercado = bool(p["unidade"]) or any(casa_encarte([k.replace(" ", "") for k in p["exige"]], o["item"]) for o in sup)
    online = [(o["unitario"] if p["unidade"] else o["liquido"]) for o in item["ofertas"] if not o.get("fisico")]
    valor_online = min(online) if online else None
    extras = {"links": links_de_pesquisa(texto), "lidas": LIDAS_PELO_ROBO, "supermercado": supermercado, "valor_online": valor_online}
    if supermercado:
        extras["encartes"] = encartes(texto, p, sup, valor_online)
    pasta = RAIZ / "docs" / "buscas"
    pasta.mkdir(parents=True, exist_ok=True)
    s = slug(texto)
    (pasta / f"{s}.json").write_text(json.dumps({"consulta": texto, "gerado_em_ms": int(time.time() * 1000),
                                                  "gerado_em": radar.AGORA, "item": item, **extras}, ensure_ascii=False), encoding="utf-8")
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
        saida = os.environ.get("GITHUB_OUTPUT")
        if saida:
            with open(saida, "a", encoding="utf-8") as f:
                f.write(f"id={p['id']}\n")
    elif ACAO == "incluir_lista":
        novos, repetidos, vistos = [], [], {x["id"] for x in cfg["produtos"]}
        for linha in itens_da_lista(TEXTO):
            texto, alvo = separa_alvo(linha)
            p = produto_de_texto(texto, alvo)
            if p["id"] in vistos:
                repetidos.append(p["nome"])
                continue
            vistos.add(p["id"])
            cfg["produtos"].append(p)
            novos.append(p)
        if not novos:
            sys.exit("nenhum item novo (todos já estavam na lista): " + "; ".join(repetidos))
        salva(cfg)
        for p in novos:
            print(f"incluído: {p['nome']} (exige {p['exige']}, unidade {p['unidade']})")
        for nome in repetidos:
            print(f"já estava na lista: {nome}")
        saida = os.environ.get("GITHUB_OUTPUT")
        if saida:
            with open(saida, "a", encoding="utf-8") as f:
                f.write("id=" + " ".join(p["id"] for p in novos) + "\n")
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
