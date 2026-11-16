"""Radar de compras: coleta preços, lê o termômetro e o histórico do Zoom/Buscapé, sugere o caminho
mais barato (portal de cashback + pagamento + clube), avisa por ntfy e gera o painel.

Uso: python radar.py              (todos os produtos)
     python radar.py cafe-gourmet  (só um produto, para teste; não gera painel)
Variáveis: NTFY_TOPICO (alerta), RADAR_SEM_ALERTA=1 (não envia aviso).
"""
import csv
import json
import os
import re
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from curl_cffi import requests as http

RAIZ = Path(__file__).parent
DADOS = RAIZ / "dados"
DOCS = RAIZ / "docs"
FUSO = timezone(timedelta(hours=-3))
HOJE = datetime.now(FUSO).strftime("%Y-%m-%d")
AGORA = datetime.now(FUSO).strftime("%Y-%m-%d %H:%M")
MAX_OFERTAS = 12
PAGINAS_POR_PRODUTO = 3
BLACK_FRIDAY = "2026-11-27"


# ---------- utilidades ----------
def norm(texto):
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def baixar(url):
    for _ in range(2):
        try:
            r = http.get(url, impersonate="chrome", timeout=30, headers={"Accept-Language": "pt-BR,pt;q=0.9"})
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code}: {url[:80]}")
        except Exception as erro:  # noqa: BLE001
            print(f"  falha {type(erro).__name__}: {str(erro)[:100]}")
        time.sleep(2)
    return None


def dinheiro(texto):
    try:
        return float(texto.replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def estado_next(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))["props"]["initialReduxState"]
    except (KeyError, ValueError):
        return None


MEDIDAS = {"kg": ("kg", 1.0), "g": ("kg", 0.001), "gr": ("kg", 0.001), "grs": ("kg", 0.001),
           "l": ("l", 1.0), "lt": ("l", 1.0), "litro": ("l", 1.0), "litros": ("l", 1.0),
           "ml": ("l", 0.001)}


def quantidade(titulo, unidade):
    """Total no título em kg ou litros (None se não achar). Entende '2 x 500g', 'kit 6', 'c/ 12'."""
    t = norm(titulo).replace(",", ".")
    base = None
    for valor, un in re.findall(r"(\d+(?:\.\d+)?)\s*(kg|gr|grs|g|litros|litro|lt|ml|l)(?![a-z0-9])", t):
        tipo, fator = MEDIDAS[un]
        if tipo == unidade:
            base = float(valor) * fator
            break
    if not base:
        return None
    mult = 1
    m = (re.search(r"(\d+)\s*x\s*\d+(?:\.\d+)?\s*(?:kg|g|ml|l)(?![a-z0-9])", t)
         or re.search(r"(?:kit|c/|com|caixa com|fardo)\s*(\d+)(?![0-9])", t)
         or re.search(r"(?<![0-9])(\d+)\s*(?:unidades|un|und)(?![a-z])", t))
    if m:
        mult = max(1, min(int(m.group(1)), 60))
    return base * mult


# ---------- fontes ----------
CACHE_PAGINAS = {}


def fonte_zoom(busca):
    """Zoom (mesma base do Buscapé e Bondfaro). Devolve produtos com o caminho da página para ler o termômetro."""
    for dominio in ("www.zoom.com.br", "www.buscape.com.br", "www.bondfaro.com.br"):
        est = estado_next(baixar(f"https://{dominio}/search?q={quote_plus(busca)}"))
        if not est:
            continue
        saida = []
        for h in est.get("hits", {}).get("hits", []):
            if h.get("type") not in (None, "product", "offer") or not h.get("price"):
                continue
            melhor = h.get("bestOffer") or {}
            saida.append({
                "fonte": "Zoom", "loja": melhor.get("merchantName") or "?",
                "merchant_id": str(melhor.get("merchantId") or ""),
                "titulo": h.get("name") or h.get("shortName") or "",
                "preco": float(h["price"]), "dominio": dominio,
                "pagina": h.get("url") if h.get("type") == "product" else None,
                "link": f"https://{dominio}{h.get('url', '')}",
            })
        if saida:
            return saida
    return []


def fonte_amazon(busca):
    html = baixar(f"https://www.amazon.com.br/s?k={quote_plus(busca)}")
    if not html:
        return []
    saida = []
    for card in BeautifulSoup(html, "html.parser").select('div[data-component-type="s-search-result"]'):
        asin, h2 = card.get("data-asin"), card.find("h2")
        inteiro = card.select_one("span.a-price:not(.a-text-price) span.a-price-whole")
        frac = card.select_one("span.a-price:not(.a-text-price) span.a-price-fraction")
        if not (asin and h2 and inteiro):
            continue
        preco = dinheiro(inteiro.get_text("").strip().rstrip(",") + "," + (frac.get_text().strip() if frac else "00"))
        if preco:
            saida.append({"fonte": "Amazon", "loja": "Amazon", "merchant_id": "", "titulo": h2.get_text(" ", strip=True),
                          "preco": preco, "dominio": None, "pagina": None, "link": f"https://www.amazon.com.br/dp/{asin}"})
    return saida


def fonte_mercadolivre(busca):
    """API oficial do Mercado Livre. Precisa de ML_CLIENT_ID e ML_CLIENT_SECRET (app próprio, gratuito)."""
    cid, segredo = os.environ.get("ML_CLIENT_ID", "").strip(), os.environ.get("ML_CLIENT_SECRET", "").strip()
    if not (cid and segredo):
        return []
    try:
        if "token" not in CACHE_PAGINAS:
            r = http.post("https://api.mercadolibre.com/oauth/token", timeout=30,
                          data={"grant_type": "client_credentials", "client_id": cid, "client_secret": segredo},
                          headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
            if r.status_code != 200:
                print(f"  Mercado Livre: token recusado (HTTP {r.status_code})")
                return []
            CACHE_PAGINAS["token"] = r.json()["access_token"]
        r = http.get("https://api.mercadolibre.com/sites/MLB/search", timeout=30,
                     params={"q": busca, "limit": 20, "condition": "new"},
                     headers={"Authorization": f"Bearer {CACHE_PAGINAS['token']}"})
        if r.status_code != 200:
            print(f"  Mercado Livre: busca recusada (HTTP {r.status_code})")
            return []
        itens = r.json().get("results", [])
    except Exception as erro:  # noqa: BLE001
        print(f"  Mercado Livre falhou: {type(erro).__name__}")
        return []
    saida = []
    for it in itens:
        if not it.get("price"):
            continue
        inst = it.get("installments") or {}
        saida.append({"fonte": "Mercado Livre", "loja": "Mercado Livre", "merchant_id": "", "titulo": it.get("title", ""),
                      "preco": float(it["price"]), "dominio": None, "pagina": None, "marketplace": False,
                      "link": it.get("permalink", ""), "frete_gratis_ml": bool((it.get("shipping") or {}).get("free_shipping")),
                      "parcelas": f"{inst['quantity']}x de R$ {inst['amount']:.2f}".replace(".", ",") if inst.get("quantity") else None})
    return saida


FONTES = [("zoom", fonte_zoom), ("amazon", fonte_amazon)]
FONTES.append(("mercadolivre", fonte_mercadolivre))
BLOQUEADAS = set()


def pagina_produto(dominio, caminho):
    """Lê da página do produto: histórico diário (40 dias), termômetro do Zoom, taxas de cashback e todas as lojas."""
    chave = (dominio, caminho)
    if chave in CACHE_PAGINAS:
        return CACHE_PAGINAS[chave]
    est = estado_next(baixar(f"https://{dominio}{caminho}"))
    res = None
    if est:
        try:
            pid = str(est["offers"]["currentProductID"])
            dias = (est.get("priceHistory", {}).get("priceHistory", {}).get(pid, {}) or {}).get("days", [])
            tip = (est.get("priceTip", {}).get(pid, {}) or {}).get("priceTip") or {}
            res = {
                "dias": [{"d": x["date"], "v": float(x["price"])} for x in dias if x.get("price")],
                "tip": {"descricao": tip.get("description"), "mediana": tip.get("median_price")},
                "taxas": est.get("merchantCashbacks", {}).get("rates") or {},
                "lojas": [{"loja": o.get("sellerName"), "merchant_id": str(o.get("merchantId")), "preco": float(o["price"]),
                           "parcelas": o.get("numParcels"), "parcela": o.get("parcelValue"),
                           "marketplace": bool(o.get("sellerIsMarketplace"))}
                          for o in est["offers"].get("offerList", []) if o.get("price")],
            }
        except (KeyError, ValueError, TypeError):
            res = None
    time.sleep(1.5)
    CACHE_PAGINAS[chave] = res
    return res


# ---------- Méliuz (taxas públicas por categoria) ----------
SLUGS_MELIUZ = {"Magazine Luiza": "magazine-luiza", "Mercado Livre": "mercado-livre", "KaBuM!": "kabum",
                "Americanas": "americanas", "Shopee": "shopee", "Amazon": "amazon"}


def taxas_meliuz():
    """Lê da página pública de cada loja no Méliuz o % de cashback (e a tabela por categoria, quando houver)."""
    arq = DADOS / "meliuz.json"
    antigo = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}
    novo = {}
    for loja, slug in SLUGS_MELIUZ.items():
        html = baixar(f"https://www.meliuz.com.br/desconto/{slug}")
        if not html:
            if loja in antigo:
                novo[loja] = antigo[loja]
            continue
        t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
        tiers = []
        m = re.search(r"Ocultar cashback por categoria (.*?) Ativar", t)
        if m:
            tiers = [(a.strip(), float(b.replace(",", "."))) for a, b in re.findall(r"(.+?) (\d+(?:,\d+)?)%", m.group(1))]
        flat = re.search(r"Ativar (?:até )?(\d+(?:,\d+)?)% de cashback", t)
        base = float(flat.group(1).replace(",", ".")) if flat else (max(p for _, p in tiers) if tiers else 0.0)
        novo[loja] = {"base": base, "tiers": tiers, "lido_em": HOJE}
        time.sleep(1)
    arq.write_text(json.dumps(novo, ensure_ascii=False, indent=1), encoding="utf-8")
    return novo


def taxa_meliuz(tab, loja, categoria, marketplace):
    """Faixa do Méliuz conforme a categoria do produto e quem vende (loja ou marketplace). Em dúvida, a mais baixa."""
    d = tab.get(loja)
    if not d:
        return 0.0
    tiers = [(norm(rotulo), pct) for rotulo, pct in d.get("tiers", [])]
    if not tiers:
        return float(d.get("base", 0)) / 100

    def acha(trecho):
        return [pct for rotulo, pct in tiers if trecho in rotulo]
    if marketplace and acha("outras lojas"):
        return acha("outras lojas")[0] / 100
    if categoria == "eletrodomestico" and acha("eletrodom"):
        return acha("eletrodom")[0] / 100
    if categoria == "mercado":
        return (acha("demais") or [min(p for _, p in tiers)])[0] / 100
    if acha("exceto"):
        return acha("exceto")[0] / 100
    return min(p for _, p in tiers) / 100


def frete_info(loja, preco, ben):
    cfg = ben.get("frete", {}).get("lojas", {})
    f = cfg.get(loja) or cfg.get("*") or {}
    com = f.get("gratis_com")
    if com and ben.get("clubes", {}).get(com, {}).get("ativo") and preco >= f.get("gratis_acima", 0):
        return f"frete grátis com {com}", 0.0
    custo = float(f.get("frete_estimado", 0) or 0)
    return (f"frete estimado R$ {custo:.2f}" if custo else "frete a confirmar no carrinho (CEP 35720-000)"), custo


# ---------- benefícios e estratégias ----------
def desconto_loja(tabela, loja):
    for nome, pct in tabela.items():
        if nome != "*" and norm(nome) == norm(loja):
            return float(pct)
    return float(tabela.get("*", 0))


def aplica_loja(lista, loja):
    return "*" in lista or any(norm(x) == norm(loja) for x in lista)


def caminhos(loja, preco, taxa_zoom, ben, taxa_mel=0.0, fisico=False):
    """Todas as combinações portal x pagamento permitidas, da mais barata para a mais cara."""
    portais = [("", 0.0, "")]
    for nome, cfg in ben.get("portais", {}).items():
        if not cfg.get("ativo") or fisico:
            continue
        if cfg.get("fonte") == "site":
            pct = taxa_zoom * 100
        elif cfg.get("fonte") == "meliuz":
            pct = taxa_mel * 100
        else:
            pct = desconto_loja(cfg.get("lojas", {}), loja)
        if pct > 0:
            portais.append((nome, pct / 100, f"entrar pelo {nome} ({pct:.1f}% de cashback)"))
    pagos = [("", 0.0, 0.0, True, "")]
    for nome, cfg in ben.get("pagamento", {}).items():
        if not cfg.get("ativo") or (fisico and not cfg.get("presencial")):
            continue
        desc = desconto_loja(cfg.get("desconto_pct", {}), loja) if "desconto_pct" in cfg else 0.0
        cash = float(cfg.get("cashback_pct", 0)) if aplica_loja(cfg.get("lojas", ["*"]), loja) else 0.0
        if desc > 0 or cash > 0:
            partes = [f"{desc:g}% de desconto" if desc else "", f"{cash:g}% de volta" if cash else ""]
            pagos.append((nome, desc / 100, cash / 100, cfg.get("empilha_portal", True),
                          f"pagar com {nome} ({', '.join(x for x in partes if x)})"))
    if fisico:
        texto_frete, custo_frete = "compra presencial na loja (deslocamento não incluso)", 0.0
    else:
        texto_frete, custo_frete = frete_info(loja, preco, ben)
    saida = []
    for pn, prate, ptxt in portais:
        for gn, gdesc, gcash, empilha, gtxt in pagos:
            if pn and gn and not empilha:
                continue
            pago = preco * (1 - gdesc)
            liquido = pago - pago * prate - pago * gcash
            passos = [x for x in (ptxt, gtxt) if x]
            saida.append({"portal": pn, "pagamento": gn, "liquido": round(liquido, 2), "frete": texto_frete,
                          "frete_custo": custo_frete, "passos": passos or ["preço de vitrine, sem benefício"]})
    saida.sort(key=lambda c: c["liquido"] + c["frete_custo"])
    return saida


def clubes_da_loja(loja, ben):
    return [f"{n}: {c.get('obs', '')}" for n, c in ben.get("clubes", {}).items()
            if c.get("ativo") and aplica_loja(c.get("lojas", []), loja)]


# ---------- coleta ----------
def coletar_produto(produto, ben, tab_mel, ofertas_super=()):
    exige = [norm(x).replace(" ", "") for x in produto["exige"]]
    exclui = [norm(x) for x in produto["exclui"]]
    algum = [norm(x) for x in produto.get("exige_algum", [])]
    unidade = produto.get("unidade")
    ofertas, vistos, status = [], set(), {}
    for nome_fonte, fn in FONTES:
        total = 0
        if nome_fonte in BLOQUEADAS:
            status[nome_fonte] = "bloqueada"
            continue
        for busca in produto["buscas"]:
            achadas = fn(busca)
            if not achadas and nome_fonte == "mercadolivre":
                status[nome_fonte] = "sem credencial ou recusado"
                break
            if not achadas and nome_fonte == "amazon":
                BLOQUEADAS.add(nome_fonte)  # 503 anti-robô: não insiste
                break
            for o in achadas:
                t = norm(o["titulo"])
                if not all(x in t.replace(" ", "") for x in exige) or any(x in t for x in exclui):
                    continue
                if algum and not any(x in t for x in algum):
                    continue
                chave = (o["fonte"], o["loja"], t)
                if chave in vistos:
                    continue
                vistos.add(chave)
                o["qtd"] = quantidade(o["titulo"], unidade) if unidade else None
                if unidade and not o["qtd"]:
                    continue
                ofertas.append(o)
                total += 1
            time.sleep(1.5)
        status[nome_fonte] = total

    # lê a página dos melhores produtos do Zoom: histórico, termômetro e todas as lojas
    def valor_bruto(o):
        return o["preco"] / o["qtd"] if o.get("qtd") else o["preco"]
    paginas = {}
    for o in sorted((x for x in ofertas if x.get("pagina")), key=valor_bruto):
        if len(paginas) >= PAGINAS_POR_PRODUTO:
            break
        if o["pagina"] not in paginas:
            pg = pagina_produto(o["dominio"], o["pagina"])
            if pg:
                paginas[o["pagina"]] = pg
    expandidas = []
    for o in ofertas:
        pg = paginas.get(o.get("pagina"))
        if not pg or not pg["lojas"]:
            expandidas.append(o)
            continue
        for lj in pg["lojas"]:
            novo = dict(o, loja=lj["loja"], merchant_id=lj["merchant_id"], preco=lj["preco"], marketplace=lj["marketplace"])
            novo["taxa_zoom"] = float(pg["taxas"].get(lj["merchant_id"], 0))
            if lj.get("parcelas"):
                novo["parcelas"] = f"{lj['parcelas']}x de R$ {lj['parcela']:.2f}".replace(".", ",")
            expandidas.append(novo)
    ofertas = expandidas
    for m in produto.get("manuais", []):  # preços que o robô não alcança (ex.: Mercado Livre)
        hoje = datetime.now(FUSO).date()
        idade = (hoje - datetime.strptime(m["data"], "%Y-%m-%d").date()).days
        if m.get("validade"):
            if hoje > datetime.strptime(m["validade"], "%Y-%m-%d").date():
                continue
        elif idade > 14:
            continue
        qtd = m.get("qtd") or (quantidade(m.get("titulo", produto["nome"]), unidade) if unidade else None)
        if unidade and not qtd:
            continue
        aviso = f" (confira, tem {idade} dias)" if idade > 3 and not m.get("validade") else ""
        if m.get("validade"):
            aviso = f", válido até {m['validade'][8:10]}/{m['validade'][5:7]}"
        ofertas.append({"fonte": "Manual", "loja": m["loja"], "merchant_id": "", "titulo": m.get("titulo", produto["nome"]),
                        "preco": float(m["preco"]), "link": m.get("link", ""), "qtd": qtd, "marketplace": False,
                        "fisico": bool(m.get("fisico")),
                        "manual": f"informado em {m['data']}{aviso}"})

    for sp in ofertas_super:  # folhetos de supermercado (dados/ofertas_super.json)
        if sp.get("validade", "9999") < HOJE:
            continue
        t = norm(sp["item"])
        if not all(x in t.replace(" ", "") for x in exige) or any(x in t for x in exclui):
            continue
        if algum and not any(x in t for x in algum):
            continue
        qtd = quantidade(sp["item"], unidade) if unidade else None
        if unidade and not qtd:
            continue
        v = sp["validade"]
        nota = f"folheto válido até {v[8:10]}/{v[5:7]}" + (" (só com o clube do mercado)" if sp.get("clube") else "")
        ofertas.append({"fonte": "Folheto", "loja": sp["loja"], "merchant_id": "", "titulo": sp["item"],
                        "preco": float(sp["por"]), "link": sp.get("link", ""), "qtd": qtd, "marketplace": False,
                        "fisico": True, "manual": nota})

    for o in ofertas:
        tm = taxa_meliuz(tab_mel, o["loja"], produto.get("categoria", "geral"), o.get("marketplace", False))
        cams = caminhos(o["loja"], o["preco"], o.get("taxa_zoom", 0.0), ben, tm, o.get("fisico", False))
        o["liquido"] = cams[0]["liquido"]
        o["caminho"] = cams[0]
        o["caminhos"] = cams[:3]
        o["clubes"] = clubes_da_loja(o["loja"], ben)
        o["frete"] = cams[0]["frete"]
        o["unitario"] = round(o["liquido"] / o["qtd"], 2) if o.get("qtd") else None
        o["cashback"] = round(o["preco"] - o["liquido"], 2)
    # remove preços absurdos (acessórios ou cestas disfarçados): abaixo de 30% da mediana
    if len(ofertas) >= 5:
        med = statistics.median(o["liquido"] for o in ofertas)
        ofertas = [o for o in ofertas if o["liquido"] >= med * 0.3]
    ch = (lambda o: o["unitario"]) if unidade else (lambda o: o["liquido"] + o["caminho"]["frete_custo"])
    ofertas.sort(key=ch)
    return ofertas[:MAX_OFERTAS], status, paginas


# ---------- termômetro ----------
ZONA_ZOOM = {"BEST_HISTORY_PRICE": "baixo", "LOW_PRICE": "baixo", "GOOD_PRICE": "baixo",
             "REGULAR_PRICE": None, "HIGH_PRICE": "alto"}


def termometro(valor_atual, serie, tip):
    """Posiciona o preço de hoje dentro do histórico: baixo, normal ou alto (mesma ideia do Buscapé)."""
    valores = [x["v"] for x in serie]
    if len(valores) < 7:
        return {"zona": "formando", "dias": len(valores), "serie": serie}
    ordenados = sorted(valores)
    q25, q75 = ordenados[len(ordenados) // 4], ordenados[(3 * len(ordenados)) // 4]
    mediana = statistics.median(valores)
    mn, mx = min(valores), max(valores)
    if mx == mn:
        zona, pos = "normal", 0.5
    else:
        pos = max(0.0, min(1.0, (valor_atual - mn) / (mx - mn)))
        if valor_atual <= q25 and valor_atual <= mediana * 0.97:
            zona = "baixo"
        elif valor_atual >= q75 and valor_atual >= mediana * 1.03:
            zona = "alto"
        else:
            zona = "normal"
    desc = (tip or {}).get("descricao")
    if desc not in ZONA_ZOOM:
        print(f"  aviso: termômetro do Zoom com valor novo: {desc}")
    zona_zoom = ZONA_ZOOM.get(desc)
    if zona_zoom == "baixo" and zona != "baixo":
        zona = "baixo"  # o Zoom enxerga um histórico mais longo que os 40 dias
    return {"zona": zona, "pos": round(pos, 3), "min": mn, "max": mx, "mediana": round(mediana, 2),
            "q25": q25, "q75": q75, "dias": len(valores), "serie": serie,
            "vs_mediana": round((valor_atual / mediana - 1) * 100, 1), "no_minimo": valor_atual <= mn * 1.02,
            "zoom": (tip or {}).get("descricao")}


def serie_do_produto(produto, melhor, paginas, estado):
    """Série de preços: 40 dias do Zoom quando há página; senão, o histórico próprio acumulado."""
    pg = paginas.get(melhor.get("pagina"))
    if pg and len(pg["dias"]) >= 7:
        return pg["dias"], pg["tip"], melhor["preco"], "zoom"
    dias = estado.get(produto["id"], {}).get("dias", {})
    serie = [{"d": d, "v": x["valor"]} for d, x in sorted(dias.items()) if d != HOJE]
    atual = melhor["unitario"] if produto.get("unidade") else melhor["liquido"]
    return serie + [{"d": HOJE, "v": atual}], None, atual, "proprio"


def registrar_dia(produto, melhor, estado):
    dias = estado.setdefault(produto["id"], {}).setdefault("dias", {})
    valor = melhor["unitario"] if produto.get("unidade") else melhor["liquido"]
    atual = dias.get(HOJE)
    if atual is None or valor < atual["valor"]:
        dias[HOJE] = {"valor": valor, "preco": melhor["preco"], "loja": melhor["loja"], "titulo": melhor["titulo"]}
    return valor


# ---------- "Quer pagar mais barato?" ----------
def sinais_de_alerta(produto, termo, valor_final):
    sinais = []
    alvo = produto.get("preco_alvo")
    if alvo and valor_final <= alvo:
        sinais.append(f"abaixo do seu alvo (R$ {alvo:.2f})")
    if termo.get("zona") == "baixo":
        sinais.append(f"FAIXA BAIXA do histórico ({abs(termo['vs_mediana']):.0f}% abaixo do normal)")
        if termo.get("no_minimo"):
            sinais.append(f"no mínimo de {termo['dias']} dias")
        if termo.get("zoom") == "BEST_HISTORY_PRICE":
            sinais.append("o Zoom marca como melhor preço do histórico")
    return sinais


def ntfy(texto, topico):
    try:
        r = http.post(f"https://ntfy.sh/{topico}", data=texto.encode("utf-8"), timeout=20,
                      headers={"Priority": "4", "Tags": "shopping_cart"})
        return r.status_code == 200
    except Exception as erro:  # noqa: BLE001
        print(f"  ntfy falhou: {type(erro).__name__}")
        return False


def texto_alerta(produto, melhor, valor, sinais):
    un = f"/{produto['unidade']}" if produto.get("unidade") else ""
    cam = melhor["caminho"]
    linhas = [f"{produto['nome']}: R$ {valor:.2f}{un} na {melhor['loja']}", " + ".join(sinais),
              f"Melhor caminho: {'; '.join(cam['passos'])} = R$ {melhor['liquido']:.2f} líquido "
              f"(economia R$ {melhor['preco'] - melhor['liquido']:.2f})"]
    if melhor.get("clubes"):
        linhas.append("Clube: " + " | ".join(melhor["clubes"]))
    linhas.append(melhor["link"])
    return "\n".join(linhas)


def destaques_super(ofertas, minimo):
    """Folhetos de supermercado válidos hoje, com 'de/por', do maior desconto para o menor."""
    saida = []
    for o in ofertas:
        if o.get("validade", "9999") < HOJE or not o.get("de"):
            continue
        pct = (1 - o["por"] / o["de"]) * 100
        saida.append(dict(o, pct=round(pct, 1), forte=pct >= minimo))
    saida.sort(key=lambda o: -o["pct"])
    return saida[:40]


def analisar(p, ben, tab_mel, ofertas_super, estado, csv_out=None, topico="", alertar=True):
    """Coleta, mede o termômetro, monta estratégias e (opcional) grava CSV e avisa. Devolve o item do painel."""
    print(f"== {p['nome']}")
    ofertas, status, paginas = coletar_produto(p, ben, tab_mel, ofertas_super)
    print(f"  fontes: {status} | ofertas válidas: {len(ofertas)} | páginas lidas: {len(paginas)}")
    item = {"id": p["id"], "nome": p["nome"], "unidade": p.get("unidade"), "alvo": p.get("preco_alvo"),
            "fontes": status, "ofertas": ofertas, "sinais": [], "termometro": None, "estrategias": []}
    if not ofertas:
        return item
    melhor = ofertas[0]
    registrar_dia(p, melhor, estado)
    serie, tip, atual, origem = serie_do_produto(p, melhor, paginas, estado)
    termo = termometro(atual, serie, tip)
    termo["origem"] = origem
    valor_final = melhor["unitario"] if p.get("unidade") else melhor["liquido"]
    sinais = sinais_de_alerta(p, termo, valor_final)
    est = []
    for o in ofertas[:6]:  # estratégias: melhores caminhos entre as 6 melhores ofertas
        for c in o["caminhos"][:2]:
            est.append({"loja": o["loja"], "link": o["link"], "preco": o["preco"], "liquido": c["liquido"],
                        "passos": c["passos"], "clubes": o["clubes"], "frete": c["frete"],
                        "frete_custo": c["frete_custo"], "manual": o.get("manual")})
    est.sort(key=lambda x: x["liquido"] + x["frete_custo"])
    item.update({"termometro": termo, "sinais": sinais, "valor": valor_final, "estrategias": est[:4]})
    if csv_out is not None:
        for o in ofertas:
            csv_out.writerow([AGORA, p["id"], o["fonte"], o["loja"], o["titulo"], o["preco"], o["liquido"],
                              o["unitario"] or "", termo["zona"], o["link"]])
    ult = estado[p["id"]].get("alertado")
    limite = (datetime.now(FUSO) - timedelta(days=3)).strftime("%Y-%m-%d")
    if alertar and sinais and topico and not os.environ.get("RADAR_SEM_ALERTA"):
        novo = not ult or valor_final < ult["valor"] * 0.98 or ult["dia"] < limite
        if novo and ntfy(texto_alerta(p, melhor, valor_final, sinais), topico):
            estado[p["id"]]["alertado"] = {"valor": valor_final, "dia": HOJE}
    return item


# ---------- principal ----------
def main():
    DADOS.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    config = json.loads((RAIZ / "produtos.json").read_text(encoding="utf-8"))
    ben = json.loads((RAIZ / "beneficios.json").read_text(encoding="utf-8"))
    arq_estado = DADOS / "estado.json"
    estado = json.loads(arq_estado.read_text(encoding="utf-8")) if arq_estado.exists() else {}
    filtro = sys.argv[1:]
    topico = os.environ.get("NTFY_TOPICO", "").strip()
    novo_csv = not (DADOS / "precos.csv").exists()
    painel = []
    arq_super = DADOS / "ofertas_super.json"
    ofertas_super = json.loads(arq_super.read_text(encoding="utf-8")).get("ofertas", []) if arq_super.exists() else []
    tab_mel = taxas_meliuz()
    with open(DADOS / "precos.csv", "a", newline="", encoding="utf-8") as f:
        csv_out = csv.writer(f)
        if novo_csv:
            csv_out.writerow(["data", "produto", "fonte", "loja", "titulo", "preco", "liquido", "unitario", "zona", "link"])
        for p in config["produtos"]:
            if filtro and p["id"] not in filtro:
                continue
            painel.append(analisar(p, ben, tab_mel, ofertas_super, estado, csv_out, topico))
    arq_estado.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    if not filtro:
        modelo = (RAIZ / "painel_modelo.html").read_text(encoding="utf-8")
        dados = json.dumps({"atualizado": AGORA, "produtos": painel, "black_friday": BLACK_FRIDAY,
                            "hoje": HOJE, "beneficios": ben, "meliuz": tab_mel,
                            "super": destaques_super(ofertas_super, config.get("super", {}).get("desconto_minimo", 30))}, ensure_ascii=False)
        (DOCS / "index.html").write_text(modelo.replace("/*DADOS*/null", dados), encoding="utf-8")
        print("painel atualizado")


if __name__ == "__main__":
    main()
